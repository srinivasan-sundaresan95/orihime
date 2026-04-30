package com.example.ctor

data class Point(val x: Int, val y: Int)

class Rectangle(private val topLeft: Point, private val bottomRight: Point) {
    companion object {
        fun unit(): Rectangle = Rectangle(Point(0, 0), Point(1, 1))
    }
}

class ShapeFactory {
    fun makeRect(x1: Int, y1: Int, x2: Int, y2: Int): Rectangle {
        val tl = Point(x1, y1)
        val br = Point(x2, y2)
        return Rectangle(tl, br)
    }
}
