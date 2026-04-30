package com.example;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WalletControllerDI {
    private final WalletService walletService;

    public WalletControllerDI(WalletService walletService) {
        this.walletService = walletService;
    }

    @GetMapping("/wallet/balance")
    public String callEndpoint() {
        return walletService.getBalance();
    }
}
