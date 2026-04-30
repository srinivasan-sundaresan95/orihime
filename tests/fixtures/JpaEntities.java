package com.example.jpa;

import javax.persistence.*;
import java.util.List;

@Entity
public class Order {
    @Id
    private Long id;

    @ManyToOne(fetch = FetchType.EAGER)
    private Customer customer;

    @OneToMany(mappedBy = "order")
    private List<OrderItem> items;

    @OneToOne
    private Shipment shipment;
}

@Entity
public class Customer {
    @Id
    private Long id;

    @OneToMany(fetch = FetchType.LAZY)
    private List<Order> orders;
}

@Entity
public class OrderItem {
    @Id
    private Long id;

    @ManyToOne
    private Order order;
}

@Entity
public class Shipment {
    @Id
    private Long id;
}
