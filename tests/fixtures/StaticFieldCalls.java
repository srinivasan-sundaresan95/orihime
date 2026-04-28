package com.example;

public class StaticFieldCalls {
    public void caller() {
        Helper.INSTANCE.doWork();
        Logger.log.info("hello");
    }
}

class Helper {
    public static final Helper INSTANCE = new Helper();
    public void doWork() {}
}
