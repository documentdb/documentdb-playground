"""Beanie CRUD/compatibility test against DocumentDB.

Usage:
    MONGO_URI="mongodb://user:pass@host:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true" \
        python beanie_crud_test.py

Or pass the URI as the first argument:
    python beanie_crud_test.py "mongodb://user:pass@host:10260/?..."

Exercises connect, index creation, insert, find, update, aggregation,
unique-index enforcement, and delete using Beanie and PyMongo's
``AsyncMongoClient`` against the DocumentDB gateway. Exits non-zero on the
first real failure.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from beanie import Document, init_beanie
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError

URI = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("MONGO_DB", "beanie_test")
TLS_INSECURE = os.environ.get("TLS_INSECURE", "true").lower() != "false"

passed = 0
failed = 0


def _ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  \u2705 {name}")


def _fail(name: str, err: object) -> None:
    global failed
    failed += 1
    print(f"  \u274C {name}: {err}", file=sys.stderr)


async def step(name: str, fn) -> None:
    try:
        await fn()
        _ok(name)
    except Exception as err:  # noqa: BLE001 - report any failure and continue
        _fail(name, err)


def sanitize_uri(uri: str) -> str:
    return re.sub(r"[?&]replicaSet=[^&]*", "", uri)


class Widget(Document):
    sku: str
    name: str
    tags: List[str] = Field(default_factory=list)
    price: Optional[float] = None
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "widgets"
        indexes = [
            IndexModel([("sku", ASCENDING)], unique=True),
            IndexModel([("name", ASCENDING), ("price", DESCENDING)]),
        ]


async def run() -> int:
    if not URI:
        print("MONGO_URI not provided. Pass it as $1 or set MONGO_URI.", file=sys.stderr)
        return 2

    print("Beanie DocumentDB compatibility test")
    print("====================================")

    # Fresh collection per run keeps the test idempotent.
    Widget.Settings.name = f"widgets_{int(time.time() * 1000)}"

    client: Optional[AsyncMongoClient] = None
    connected = False

    async def _connect() -> None:
        nonlocal client, connected
        client = AsyncMongoClient(
            sanitize_uri(URI),
            directConnection=True,
            tls=True,
            tlsAllowInvalidCertificates=TLS_INSECURE,
            serverSelectionTimeoutMS=15000,
        )
        await init_beanie(database=client[DB_NAME], document_models=[Widget])
        ping = await client.admin.command("ping")
        if ping.get("ok") != 1:
            raise RuntimeError("ping did not return ok:1")
        connected = True

    await step("connect", _connect)

    if not connected:
        print("\nConnection failed; aborting remaining steps.", file=sys.stderr)
        return 1

    coll = Widget.get_pymongo_collection()

    async def _create_indexes() -> None:
        await coll.create_indexes(
            [
                IndexModel([("sku", ASCENDING)], unique=True),
                IndexModel([("name", ASCENDING), ("price", DESCENDING)]),
            ]
        )

    await step("create indexes", _create_indexes)

    created_id = None

    async def _insert_one() -> None:
        nonlocal created_id
        widget = Widget(sku="SKU-001", name="Gizmo", tags=["alpha", "beta"], price=9.99)
        await widget.insert()
        created_id = widget.id
        if created_id is None:
            raise RuntimeError("no _id returned")

    await step("insert_one (Document.insert)", _insert_one)

    async def _insert_many() -> None:
        res = await Widget.insert_many(
            [
                Widget(sku="SKU-002", name="Gadget", tags=["beta"], price=19.5),
                Widget(sku="SKU-003", name="Widget", tags=["alpha", "gamma"], price=4.25),
            ]
        )
        if len(res.inserted_ids) != 2:
            raise RuntimeError(f"expected 2 inserted, got {len(res.inserted_ids)}")

    await step("insert_many", _insert_many)

    async def _get_by_id() -> None:
        doc = await Widget.get(created_id)
        if doc is None or doc.sku != "SKU-001":
            raise RuntimeError("document not found or mismatched")

    await step("get by _id (Document.get)", _get_by_id)

    async def _find_filter_sort_limit() -> None:
        docs = await Widget.find({"price": {"$gte": 5}}).sort("-price").limit(10).to_list()
        if len(docs) != 2:
            raise RuntimeError(f"expected 2 docs, got {len(docs)}")
        if docs[0].price < docs[1].price:
            raise RuntimeError("sort order incorrect")

    await step("find with filter + sort + limit", _find_filter_sort_limit)

    async def _count() -> None:
        n = await Widget.find({}).count()
        if n != 3:
            raise RuntimeError(f"expected 3 docs, got {n}")

    await step("count_documents", _count)

    async def _update_one() -> None:
        res = await coll.update_one({"sku": "SKU-002"}, {"$set": {"price": 21}})
        if res.modified_count != 1:
            raise RuntimeError(f"expected 1 modified, got {res.modified_count}")

    await step("update_one ($set)", _update_one)

    async def _find_one_and_update() -> None:
        doc = await coll.find_one_and_update(
            {"sku": "SKU-003"},
            {"$push": {"tags": "delta"}},
            return_document=ReturnDocument.AFTER,
        )
        if not doc or "delta" not in doc.get("tags", []):
            raise RuntimeError("update not applied")

    await step("find_one_and_update (returns new)", _find_one_and_update)

    async def _aggregate() -> None:
        stats = await Widget.aggregate(
            [
                {"$unwind": "$tags"},
                {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        ).to_list()
        if not stats:
            raise RuntimeError("aggregation returned no results")

    await step("aggregation ($unwind/$group)", _aggregate)

    async def _unique_index() -> None:
        try:
            await Widget(sku="SKU-001", name="Duplicate").insert()
        except DuplicateKeyError:
            return
        raise RuntimeError("duplicate insert was not rejected")

    await step("unique index enforcement (duplicate sku rejected)", _unique_index)

    async def _delete_one() -> None:
        res = await coll.delete_one({"sku": "SKU-002"})
        if res.deleted_count != 1:
            raise RuntimeError(f"expected 1 deleted, got {res.deleted_count}")

    await step("delete_one", _delete_one)

    # Vector search: DocumentDB supports a `cosmosSearch` vector index and the
    # `$vectorSearch` aggregation stage. The index is created with the raw
    # `createIndexes` command since Beanie does not model vector indexes.
    db_ref = client[DB_NAME]
    vec_name = f"vectors_{int(time.time() * 1000)}"
    vectors = db_ref[vec_name]

    async def _vector_index_insert() -> None:
        await vectors.insert_many(
            [
                {"name": "a", "v": [1, 0, 0]},
                {"name": "b", "v": [0.9, 0.1, 0]},
                {"name": "c", "v": [0, 0, 1]},
            ]
        )
        res = await db_ref.command(
            {
                "createIndexes": vec_name,
                "indexes": [
                    {
                        "name": "v_ivf",
                        "key": {"v": "cosmosSearch"},
                        "cosmosSearchOptions": {
                            "kind": "vector-ivf",
                            "numLists": 1,
                            "similarity": "COS",
                            "dimensions": 3,
                        },
                    }
                ],
            }
        )
        if res.get("ok") != 1:
            raise RuntimeError("createIndexes did not return ok:1")

    await step("vector index + insert (cosmosSearch vector-ivf)", _vector_index_insert)

    async def _vector_search() -> None:
        # Unlike Beanie's Document.aggregate(), PyMongo's AsyncCollection.aggregate()
        # is a coroutine, so await it to get the cursor.
        cursor = await vectors.aggregate(
            [
                {
                    "$vectorSearch": {
                        "index": "v_ivf",
                        "path": "v",
                        "queryVector": [1, 0, 0],
                        "numCandidates": 10,
                        "limit": 2,
                    }
                },
                {"$project": {"name": 1, "_id": 0}},
            ]
        )
        hits = await cursor.to_list(length=10)
        if not hits:
            raise RuntimeError("vector search returned no results")
        if hits[0].get("name") != "a":
            raise RuntimeError(f"expected nearest 'a', got {hits[0].get('name')!r}")

    await step("$vectorSearch returns nearest neighbor", _vector_search)

    async def _vector_cleanup() -> None:
        await vectors.drop()

    await step("vector cleanup (drop collection)", _vector_cleanup)

    async def _drop() -> None:
        await coll.drop()

    await step("cleanup (drop collection)", _drop)

    if client is not None:
        await client.close()

    print("\n====================================")
    print(f"Passed: {passed}  Failed: {failed}")
    return 0 if failed == 0 else 1


def main() -> None:
    try:
        code = asyncio.run(run())
    except Exception as err:  # noqa: BLE001
        print(f"Unexpected error: {err}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
