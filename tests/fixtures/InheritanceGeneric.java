package com.example.generic;

import java.util.Comparator;

public class NameComparator extends Object implements Comparator<String> {
    @Override
    public int compare(String a, String b) { return a.compareTo(b); }
}
