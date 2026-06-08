#!/usr/bin/env python3
import argparse
from pathlib import Path


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def source_file(index):
    return f'''def helper_{index}(value):
    return value + {index}


def parse_payload(payload):
    total = 0
    for item in payload:
        total += helper_{index}(item)
    return total


def module_{index}_noise(payload):
    return sum(payload) + {index}
'''


def hot_sources():
    return {
        "src/core/expensive_serializer.py": '''import json


def expensive_serializer(records):
    output = []
    for record in records:
        output.append(json.dumps(record, sort_keys=True))
    return output
''',
        "src/core/regex_parser.py": '''import re


def regex_parser(lines):
    total = 0
    for line in lines:
        if re.compile(r"^[A-Z]+-[0-9]+:").match(line):
            total += 1
    return total
''',
        "src/core/allocation_hot_loop.py": '''def allocation_hot_loop(values):
    result = []
    for value in values:
        result.append({"value": value, "square": value * value})
    return result
''',
        "src/core/business_logic.py": '''def calculate_invoice_total(lines):
    return sum(line["amount"] for line in lines)


def apply_discount(total, rate):
    return total * (1 - rate)
''',
    }


def mixed_runtime_sources():
    return {
        "src/dotnet/OrderController.cs": '''namespace Checkout.Api.Controllers;

public sealed class OrderController
{
    public string SerializeResponse(object response)
    {
        return System.Text.Json.JsonSerializer.Serialize(response);
    }
}
''',
        "src/java/com/example/RegexParser.java": '''package com.example;

import java.util.regex.Pattern;

public final class RegexParser {
    public boolean parseLine(String line) {
        return Pattern.compile("^[A-Z]+-[0-9]+:").matcher(line).find();
    }
}
''',
        "src/spark/com/example/spark/TransformJob.scala": '''package com.example.spark

object TransformJob {
  def materializeRows(values: Seq[Int]): Seq[(Int, Int)] = {
    values.map(value => (value, value * value)).toList
  }
}
''',
        "src/flink/com/example/flink/WindowAggregator.java": '''package com.example.flink;

import java.util.ArrayList;
import java.util.List;

public final class WindowAggregator {
    public List<Integer> aggregateWindow(List<Integer> values) {
        List<Integer> result = new ArrayList<>();
        for (Integer value : values) {
            result.add(value * value);
        }
        return result;
    }
}
''',
        "src/go/handler.go": '''package main

import "encoding/json"

func handleRequest(value map[string]any) ([]byte, error) {
    return json.Marshal(value)
}
''',
    }


def folded_profile(noise_count, mixed_runtimes, noise_frame_cardinality):
    lines = [
        "root;api_handler;load_customer;expensive_serializer 900000",
        "root;api_handler;load_customer;expensive_serializer;json.dumps 650000",
        "root;ingest_worker;normalize;regex_parser 500000",
        "root;ingest_worker;normalize;regex_parser;re.compile 420000",
        "root;batch_worker;materialize;allocation_hot_loop 380000",
        "root;batch_worker;materialize;allocation_hot_loop;list.append 190000",
        "root;billing;calculate_invoice_total 40000",
        "root;billing;apply_discount 12000",
        "root;ambiguous;parse_payload 250000",
    ]
    if mixed_runtimes:
        lines.extend(
            [
                "root;dotnet;Checkout.Api.Controllers.OrderController.SerializeResponse 330000",
                "root;dotnet;System.Text.Json.JsonSerializer.Serialize 210000",
                "root;jvm;com.example.RegexParser.parseLine 280000",
                "root;jvm;java.util.regex.Pattern.compile 180000",
                "root;spark;com.example.spark.TransformJob.materializeRows 250000",
                "root;flink;com.example.flink.WindowAggregator.aggregateWindow 230000",
                "root;go;main.handleRequest 220000",
                "root;go;encoding/json.Marshal 160000",
            ]
        )
    for index in range(noise_count):
        weight = 1
        lines.append(f"root;noise_layer_{index % 31};module_{index % noise_frame_cardinality}_noise {weight}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--service", default="complex-fixture")
    parser.add_argument("--noise-files", type=int, default=160)
    parser.add_argument("--noise-stacks", type=int, default=20000)
    parser.add_argument("--noise-frame-cardinality", type=int, default=160)
    parser.add_argument("--mixed-runtimes", action="store_true")
    args = parser.parse_args()

    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for relative, text in hot_sources().items():
        write(root / relative, text)
    if args.mixed_runtimes:
        for relative, text in mixed_runtime_sources().items():
            write(root / relative, text)
    for index in range(args.noise_files):
        write(root / "src" / "noise" / f"module_{index}.py", source_file(index))
    write(
        root / "pyroscope-agent.yaml",
        f"""schema_version: 1
service_name: {args.service}
runtime: python
dockerfile: Dockerfile
source:
  roots: ["src"]
  exclude: ["test", "tests", "generated", "vendor", "node_modules", "bin", "obj", "target"]
""",
    )
    cardinality = max(1, args.noise_frame_cardinality)
    write(root / "profiles" / "complex.folded", folded_profile(args.noise_stacks, args.mixed_runtimes, cardinality))
    print(root)


if __name__ == "__main__":
    main()
