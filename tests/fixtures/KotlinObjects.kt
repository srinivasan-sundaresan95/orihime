package com.example

object DateTimeUtil {
    fun isInTimePeriod(value: Int): Boolean = value > 0
    fun formatDate(date: String): String = date
}

class SomeService {
    companion object {
        fun create(): SomeService = SomeService()
    }

    fun doWork() {
        val result = DateTimeUtil.isInTimePeriod(5)
    }
}
