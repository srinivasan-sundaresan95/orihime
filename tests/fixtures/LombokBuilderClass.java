package com.example.lombok;

import lombok.Builder;

@Builder
public class LombokBuilderClass {
    private String title;

    public static LombokBuilderClass builder() {
        return new LombokBuilderClass();
    }

    public LombokBuilderClass build() {
        return this;
    }

    public void validate() {
    }
}
