package com.example

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/v5")
class ArrayAnnotationController {

    // Array-literal syntax: @GetMapping(value = ["/point_card"])
    @GetMapping(value = ["/point_card"])
    fun getPointCard(): String = "ok"

    // Array-literal syntax with path= key
    @PostMapping(path = ["/point_card/update"])
    fun updatePointCard(): String = "ok"

    // Plain positional string — must still work
    @GetMapping("/health")
    fun healthCheck(): String = "ok"
}
