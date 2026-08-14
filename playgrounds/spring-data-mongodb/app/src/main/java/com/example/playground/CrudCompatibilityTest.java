package com.example.playground;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import com.example.playground.support.MongoClientFactory;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoCollection;
import org.bson.Document;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.data.annotation.Id;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.FindAndModifyOptions;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.aggregation.Aggregation;
import org.springframework.data.mongodb.core.aggregation.AggregationResults;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;

/**
 * Standalone Spring Data MongoDB CRUD/compatibility suite for DocumentDB.
 *
 * <p>Boots a bare {@link MongoTemplate} (no Spring Boot context) and exercises
 * connect, index creation, insert, query, update, aggregation, unique-index
 * enforcement, delete, and vector search — mirroring the sibling playgrounds.
 * Prints a {@code Passed: N  Failed: N} summary and exits non-zero on failure.
 */
public final class CrudCompatibilityTest {

    private static int passed = 0;
    private static int failed = 0;

    /** Test-only POJO for the typed Spring Data operations. */
    public static class Widget {
        @Id
        public String id;
        public String sku;
        public String name;
        public List<String> tags;
        public Double price;
        public Boolean active;
        public Instant createdAt;
    }

    private interface Step {
        void run() throws Exception;
    }

    private static boolean isOk(Document result) {
        Object ok = result.get("ok");
        return ok instanceof Number && ((Number) ok).doubleValue() == 1.0;
    }

    private static void step(String name, Step action) {
        try {
            action.run();
            passed++;
            System.out.println("  \u2705 " + name);
        } catch (Exception | AssertionError ex) {
            failed++;
            System.out.println("  \u274C " + name + ": " + ex.getMessage());
        }
    }

    public static void main(String[] args) {
        String rawUri = args.length > 0 ? args[0] : System.getenv("MONGO_URI");
        boolean insecure = MongoClientFactory.insecureFromEnv();
        String dbName = System.getenv().getOrDefault("MONGO_DB", "springdata_test");

        System.out.println("Spring Data MongoDB DocumentDB compatibility test");
        System.out.println("=================================================");

        MongoClient client = MongoClientFactory.create(rawUri, insecure);
        MongoTemplate template = new MongoTemplate(client, dbName);

        long stamp = System.currentTimeMillis();
        String collection = "widgets_" + stamp;
        String vectorCollection = "vectors_" + stamp;

        step("connect", () -> {
            Document ping = template.executeCommand(new Document("ping", 1));
            if (!isOk(ping)) {
                throw new AssertionError("ping did not return ok:1");
            }
        });

        step("create indexes", () -> {
            template.indexOps(collection).ensureIndex(
                    new Index().on("sku", Sort.Direction.ASC).unique().named("sku_unique"));
            template.indexOps(collection).ensureIndex(
                    new Index().on("name", Sort.Direction.ASC)
                            .on("price", Sort.Direction.DESC).named("name_price"));
        });

        step("insert (single)", () -> {
            Widget widget = new Widget();
            widget.sku = "SKU-001";
            widget.name = "Gizmo";
            widget.tags = new ArrayList<>(List.of("alpha", "beta"));
            widget.price = 9.99;
            widget.active = true;
            widget.createdAt = Instant.now();
            Widget saved = template.insert(widget, collection);
            if (saved.id == null) {
                throw new AssertionError("no _id assigned");
            }
        });

        step("insert (many)", () -> {
            Widget gadget = new Widget();
            gadget.sku = "SKU-002";
            gadget.name = "Gadget";
            gadget.tags = new ArrayList<>(List.of("beta"));
            gadget.price = 19.5;

            Widget widget = new Widget();
            widget.sku = "SKU-003";
            widget.name = "Widget";
            widget.tags = new ArrayList<>(List.of("alpha", "gamma"));
            widget.price = 4.25;

            template.insert(List.of(gadget, widget), collection);
        });

        step("findById", () -> {
            Widget first = template.findOne(
                    new Query(Criteria.where("sku").is("SKU-001")), Widget.class, collection);
            if (first == null) {
                throw new AssertionError("SKU-001 not found");
            }
            Widget byId = template.findById(first.id, Widget.class, collection);
            if (byId == null || !"SKU-001".equals(byId.sku)) {
                throw new AssertionError("document not found or mismatched by _id");
            }
        });

        step("find with filter + sort + limit", () -> {
            Query query = new Query(Criteria.where("price").gte(5))
                    .with(Sort.by(Sort.Direction.DESC, "price"))
                    .limit(10);
            List<Widget> docs = template.find(query, Widget.class, collection);
            if (docs.size() != 2) {
                throw new AssertionError("expected 2 docs, got " + docs.size());
            }
            if (docs.get(0).price < docs.get(1).price) {
                throw new AssertionError("sort order incorrect");
            }
        });

        step("count", () -> {
            long count = template.count(new Query(), Widget.class, collection);
            if (count != 3) {
                throw new AssertionError("expected 3 docs, got " + count);
            }
        });

        step("updateFirst ($set)", () -> {
            var result = template.updateFirst(
                    new Query(Criteria.where("sku").is("SKU-002")),
                    new Update().set("price", 21), Widget.class, collection);
            if (result.getModifiedCount() != 1) {
                throw new AssertionError("expected 1 modified, got " + result.getModifiedCount());
            }
        });

        step("findAndModify (returns new)", () -> {
            Widget updated = template.findAndModify(
                    new Query(Criteria.where("sku").is("SKU-003")),
                    new Update().push("tags", "delta"),
                    FindAndModifyOptions.options().returnNew(true),
                    Widget.class, collection);
            if (updated == null || !updated.tags.contains("delta")) {
                throw new AssertionError("update not applied");
            }
        });

        step("aggregation ($unwind/$group/$sort)", () -> {
            Aggregation aggregation = Aggregation.newAggregation(
                    Aggregation.unwind("tags"),
                    Aggregation.group("tags").count().as("count"),
                    Aggregation.sort(Sort.Direction.DESC, "count"));
            AggregationResults<Document> stats =
                    template.aggregate(aggregation, collection, Document.class);
            if (stats.getMappedResults().isEmpty()) {
                throw new AssertionError("aggregation returned no results");
            }
        });

        step("unique index enforcement (duplicate sku rejected)", () -> {
            Widget duplicate = new Widget();
            duplicate.sku = "SKU-001";
            duplicate.name = "Duplicate";
            try {
                template.insert(duplicate, collection);
            } catch (DuplicateKeyException expected) {
                return;
            }
            throw new AssertionError("duplicate insert was not rejected");
        });

        step("delete (single)", () -> {
            var result = template.remove(
                    new Query(Criteria.where("sku").is("SKU-002")), Widget.class, collection);
            if (result.getDeletedCount() != 1) {
                throw new AssertionError("expected 1 deleted, got " + result.getDeletedCount());
            }
        });

        step("vector index + insert (cosmosSearch vector-ivf)", () -> {
            MongoCollection<Document> vectors = template.getCollection(vectorCollection);
            vectors.insertMany(List.of(
                    new Document("name", "a").append("vector", List.of(1.0, 0.0, 0.0)),
                    new Document("name", "b").append("vector", List.of(0.9, 0.1, 0.0)),
                    new Document("name", "c").append("vector", List.of(0.0, 0.0, 1.0))));
            Document command = new Document("createIndexes", vectorCollection)
                    .append("indexes", List.of(new Document("name", "vector_ivf")
                            .append("key", new Document("vector", "cosmosSearch"))
                            .append("cosmosSearchOptions", new Document("kind", "vector-ivf")
                                    .append("numLists", 1)
                                    .append("similarity", "COS")
                                    .append("dimensions", 3))));
            Document result = template.executeCommand(command);
            if (!isOk(result)) {
                throw new AssertionError("createIndexes did not return ok:1");
            }
        });

        step("$vectorSearch returns nearest neighbor", () -> {
            MongoCollection<Document> vectors = template.getCollection(vectorCollection);
            Document search = new Document("$vectorSearch", new Document("index", "vector_ivf")
                    .append("path", "vector")
                    .append("queryVector", List.of(1.0, 0.0, 0.0))
                    .append("numCandidates", 10)
                    .append("limit", 2));
            Document project = new Document("$project", new Document("name", 1).append("_id", 0));

            List<Document> hits = new ArrayList<>();
            vectors.aggregate(List.of(search, project)).into(hits);
            if (hits.isEmpty()) {
                throw new AssertionError("vector search returned no results");
            }
            if (!"a".equals(hits.get(0).getString("name"))) {
                throw new AssertionError("expected nearest 'a', got '" + hits.get(0).getString("name") + "'");
            }
        });

        step("vector cleanup (drop collection)", () -> template.dropCollection(vectorCollection));
        step("cleanup (drop collection)", () -> template.dropCollection(collection));

        client.close();

        System.out.println("=================================================");
        System.out.println("Passed: " + passed + "  Failed: " + failed);
        System.exit(failed == 0 ? 0 : 1);
    }

    private CrudCompatibilityTest() {
    }
}
