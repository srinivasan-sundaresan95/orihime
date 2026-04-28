package com.example;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class DICallerClass {
    @Autowired
    private WalletService walletService;

    public String fetchBalance() {
        return walletService.getBalance();
    }
}
