package com.example.lombok;

import lombok.Data;

@Data
public class LombokDataClass {
    private String name;
    private int age;

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public int getAge() {
        return age;
    }

    public void setAge(int age) {
        this.age = age;
    }

    @Override
    public boolean equals(Object o) {
        return false;
    }

    @Override
    public int hashCode() {
        return 0;
    }

    @Override
    public String toString() {
        return "LombokDataClass";
    }

    public boolean canEqual(Object o) {
        return false;
    }

    public String processData() {
        return name.toUpperCase();
    }
}
