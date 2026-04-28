package com.example;

import org.springframework.stereotype.Service;

@Service
public class WalletServiceImpl implements WalletService {
    @Override
    public String getBalance() {
        return "100";
    }
}
