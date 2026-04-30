package com.example.ctor;

class Address {
    private String city;
    Address(String city) { this.city = city; }
}

class Person {
    private Address address;
    Person(String city) {
        this.address = new Address(city);  // constructor call
    }
    Address getAddress() {
        return new Address("Tokyo");       // constructor call in regular method
    }
}

class PersonFactory {
    Person create(String city) {
        return new Person(city);           // constructor call
    }
}
