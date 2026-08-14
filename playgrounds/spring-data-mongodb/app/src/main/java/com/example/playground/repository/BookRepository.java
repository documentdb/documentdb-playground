package com.example.playground.repository;

import java.util.List;

import com.example.playground.model.Book;
import org.springframework.data.mongodb.repository.MongoRepository;

/** Spring Data repository for {@link Book} documents. */
public interface BookRepository extends MongoRepository<Book, String> {

    /** Derived query: exercises Spring Data query generation against DocumentDB. */
    List<Book> findByAuthorOrderByCreatedAtDesc(String author);
}
