package com.example.playground.web;

import static org.springframework.data.mongodb.core.aggregation.Aggregation.group;
import static org.springframework.data.mongodb.core.aggregation.Aggregation.newAggregation;
import static org.springframework.data.mongodb.core.aggregation.Aggregation.sort;
import static org.springframework.data.mongodb.core.aggregation.Aggregation.unwind;

import java.util.List;
import java.util.Map;

import com.example.playground.model.Book;
import com.example.playground.repository.BookRepository;
import org.bson.Document;
import org.springframework.data.domain.Sort.Direction;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.aggregation.Aggregation;
import org.springframework.data.mongodb.core.aggregation.AggregationResults;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** REST API over the {@link Book} collection, mirroring the sibling playgrounds. */
@RestController
public class BookController {

    private final BookRepository books;
    private final MongoTemplate mongoTemplate;

    public BookController(BookRepository books, MongoTemplate mongoTemplate) {
        this.books = books;
        this.mongoTemplate = mongoTemplate;
    }

    @PostMapping("/books")
    public ResponseEntity<?> create(@RequestBody Book book) {
        if (isBlank(book.getTitle()) || isBlank(book.getAuthor())) {
            return ResponseEntity.badRequest().body(Map.of("error", "title and author are required"));
        }
        book.setId(null);
        if (book.getInStock() == null) {
            book.setInStock(Boolean.TRUE);
        }
        return ResponseEntity.status(201).body(books.save(book));
    }

    @GetMapping("/books")
    public ResponseEntity<?> list(@RequestParam(required = false) String author) {
        if (author != null) {
            String trimmed = author.trim();
            if (trimmed.isEmpty() || trimmed.length() > 100) {
                return ResponseEntity.badRequest()
                        .body(Map.of("error", "author must be a non-empty string up to 100 characters"));
            }
            List<Book> matches = books.findByAuthorOrderByCreatedAtDesc(trimmed);
            return ResponseEntity.ok(Map.of("count", matches.size(), "books", matches));
        }
        List<Book> all = books.findAll();
        return ResponseEntity.ok(Map.of("count", all.size(), "books", all));
    }

    @GetMapping("/books/{id}")
    public ResponseEntity<?> get(@PathVariable String id) {
        return books.findById(id)
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.status(404).body(Map.of("error", "not found")));
    }

    @PatchMapping("/books/{id}")
    public ResponseEntity<?> update(@PathVariable String id, @RequestBody Book changes) {
        return books.findById(id)
                .<ResponseEntity<?>>map(existing -> {
                    if (changes.getTitle() != null) existing.setTitle(changes.getTitle());
                    if (changes.getAuthor() != null) existing.setAuthor(changes.getAuthor());
                    if (changes.getGenres() != null) existing.setGenres(changes.getGenres());
                    if (changes.getPages() != null) existing.setPages(changes.getPages());
                    if (changes.getPublished() != null) existing.setPublished(changes.getPublished());
                    if (changes.getInStock() != null) existing.setInStock(changes.getInStock());
                    if (changes.getRating() != null) existing.setRating(changes.getRating());
                    return ResponseEntity.ok(books.save(existing));
                })
                .orElseGet(() -> ResponseEntity.status(404).body(Map.of("error", "not found")));
    }

    @DeleteMapping("/books/{id}")
    public ResponseEntity<?> delete(@PathVariable String id) {
        if (!books.existsById(id)) {
            return ResponseEntity.status(404).body(Map.of("error", "not found"));
        }
        books.deleteById(id);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/stats/genres")
    public ResponseEntity<?> genreStats() {
        Aggregation aggregation = newAggregation(
                unwind("genres"),
                group("genres").count().as("count"),
                sort(Direction.DESC, "count"));
        AggregationResults<Document> results =
                mongoTemplate.aggregate(aggregation, "books", Document.class);
        return ResponseEntity.ok(results.getMappedResults());
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }
}
