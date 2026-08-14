package com.example.playground.config;

import com.example.playground.support.MongoClientFactory;
import com.mongodb.client.MongoClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Supplies the {@link MongoClient} bean.
 *
 * <p>Providing the client explicitly lets us translate the shared DocumentDB
 * connection string into the options the MongoDB Java driver expects (see
 * {@link MongoClientFactory}). Spring Boot's Mongo auto-configuration backs off
 * when a {@code MongoClient} bean is present, and still uses
 * {@code spring.data.mongodb.database} to pick the database.
 */
@Configuration
public class MongoConfig {

    @Bean
    public MongoClient mongoClient() {
        return MongoClientFactory.fromEnv();
    }
}
