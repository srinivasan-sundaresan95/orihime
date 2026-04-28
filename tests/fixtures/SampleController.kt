package com.example

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.client.RestClient

@RestController
@RequestMapping("/api")
class SampleController {

    @GetMapping("/users/{id}")
    suspend fun getUser(id: String): String {
        return restClient.get().uri("http://user-service/internal/users/{id}").retrieve().body(String::class.java)!!
    }

    @PostMapping("/users")
    suspend fun createUser(): String {
        return "created"
    }

    private fun helperMethod() {
        // no annotation, not suspend
    }
}
