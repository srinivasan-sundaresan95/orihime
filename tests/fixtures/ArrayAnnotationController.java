package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/v5")
public class ArrayAnnotationController {

    // Array-initializer syntax: @GetMapping(value = {"/point_card"})
    @GetMapping(value = {"/point_card"})
    public String getPointCard() {
        return "ok";
    }

    // Array-initializer syntax with path= key
    @PostMapping(path = {"/point_card/update"})
    public String updatePointCard() {
        return "ok";
    }

    // Plain positional string — must still work
    @GetMapping("/health")
    public String healthCheck() {
        return "ok";
    }
}
