package com.example;
public class CallChain {
    public void methodA() { methodB(); }
    public void methodB() { methodC(); }
    public void methodC() { }
}
