package com.example.playground.web;

import java.util.Map;

import org.bson.Document;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Liveness endpoint that pings DocumentDB through Spring Data. */
@RestController
public class HealthController {

    private final MongoTemplate mongoTemplate;

    public HealthController(MongoTemplate mongoTemplate) {
        this.mongoTemplate = mongoTemplate;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> health() {
        try {
            Document result = mongoTemplate.executeCommand(new Document("ping", 1));
            Object ok = result.get("ok");
            if (ok instanceof Number && ((Number) ok).doubleValue() == 1.0) {
                return ResponseEntity.ok(Map.of("status", "healthy", "db", "connected"));
            }
        } catch (RuntimeException ex) {
            return ResponseEntity.status(503).body(Map.of("status", "unhealthy", "db", ex.getMessage()));
        }
        return ResponseEntity.status(503).body(Map.of("status", "unhealthy", "db", "disconnected"));
    }
}
