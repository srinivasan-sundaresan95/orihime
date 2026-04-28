package com.example

import java.time.ZonedDateTime

fun ZonedDateTime.isInTimePeriod(start: ZonedDateTime, end: ZonedDateTime): Boolean {
    return this.isAfter(start) && this.isBefore(end)
}

fun String.toSlug(): String = this.lowercase().replace(" ", "-")

class ScheduleService {
    fun check(dt: ZonedDateTime, start: ZonedDateTime, end: ZonedDateTime): Boolean {
        return dt.isInTimePeriod(start, end)
    }
}
