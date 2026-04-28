package com.example;

import org.springframework.stereotype.Repository;

public interface DataStore {
    void save(String data);
}

@Repository
public class RepositoryImpl implements DataStore {
    @Override
    public void save(String data) {}
}
