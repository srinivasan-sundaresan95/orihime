package com.example;

import org.springframework.stereotype.Component;

@Component
public class ComponentAdapter implements SomePort {
    @Override
    public void execute() {}
}
