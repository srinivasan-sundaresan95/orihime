package com.example;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class WalletController {

    @GetMapping(path = RequestMapping.WALLET_STATUS)
    public String getWalletStatus() {
        return "ok";
    }

    @PostMapping(RequestMapping.USER_INFO)
    public String getUserInfo() {
        return "info";
    }
}
