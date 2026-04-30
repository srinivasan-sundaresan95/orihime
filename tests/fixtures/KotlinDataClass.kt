package com.example.kotlin

data class KotlinDataClass(val id: Int, val name: String) {
    fun customMethod(): String = "hello"

    override fun toString(): String = "KotlinDataClass($id)"
    fun copy(id: Int, name: String) = KotlinDataClass(id, name)
}

class RegularKotlinClass {
    fun getName(): String = "regular"
}
