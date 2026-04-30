package com.example.vd;

// Abstract base class with two abstract methods
abstract class Animal {
    abstract void speak();
    abstract void move();
    void breathe() { }  // concrete — NOT abstract, should NOT fan-out
}

// Two concrete subclasses
class Dog extends Animal {
    @Override public void speak() { }
    @Override public void move() { }
}

class Cat extends Animal {
    @Override public void speak() { }
    @Override public void move() { }
}

// A caller that invokes the abstract method on a variable of type Animal
class AnimalTrainer {
    void train(Animal a) {
        a.speak();  // virtual dispatch → should create CALLS to Dog.speak, Cat.speak
        a.move();
    }
}
