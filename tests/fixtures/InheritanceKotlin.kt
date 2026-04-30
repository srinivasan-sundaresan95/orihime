package com.example.kotlin

interface Greeter {
    fun greet()
}

open class BaseService {
    open fun process() {}
}

class ServiceImpl : BaseService(), Greeter {
    override fun greet() {}
    override fun process() {}
}

object SingletonService : BaseService() {
    fun doWork() {}
}
