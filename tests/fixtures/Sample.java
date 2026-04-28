package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;

@RestController
@RequestMapping("/api")
public class SampleController {

    private RestTemplate restTemplate;

    @GetMapping("/users/{id}")
    public String getUser(int id) {
        helperMethod();
        return restTemplate.getForObject("http://user-service/internal/users/{id}", String.class);
    }

    @PostMapping("/users")
    public String createUser(String user) {
        return "created";
    }

    private String helperMethod() {
        return "helper";
    }
}
