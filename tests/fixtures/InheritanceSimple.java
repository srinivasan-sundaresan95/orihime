package com.example.inheritance;

public interface PaymentStrategy {
    void pay();
}

public interface FundStrategy extends PaymentStrategy {
    void invest();
}

public abstract class BalanceStrategy implements FundStrategy {
    public abstract void calculate();
}

public class FundBalanceStrategy extends BalanceStrategy {
    @Override public void calculate() {}
    @Override public void invest() {}
    @Override public void pay() {}
}

public class CashStrategy implements PaymentStrategy {
    @Override public void pay() {}
}
