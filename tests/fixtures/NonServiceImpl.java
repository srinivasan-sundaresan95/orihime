package com.example;

public class NonServiceImpl implements WalletService {
    @Override
    public String getBalance() {
        return "0";
    }
}
