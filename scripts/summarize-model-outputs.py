"""Export observed provider outputs and arrival timing without inventing hidden reasoning."""
import argparse
import json
from pathlib import Path


def interval(end, start):
    return round((end - start) / 1_000_000, 4) if end is not None and start is not None else None


def analyze(document):
    rows = document["events"]
    groups = {}
    for row in rows:
        if row["stream"].startswith("selector") and row.get("response_id"):
            groups.setdefault(row["response_id"], []).append(row)
    responses = []
    for identifier, events in groups.items():
        final = next((row["event"].get("response", {}) for row in reversed(events)
            if row["event"]["type"] in {"response.completed", "response.done", "response.failed", "response.incomplete"}), {})
        def timestamp(kind):
            return next((row["observed_ns"] for row in events if row["event"]["type"] == kind), None)
        arguments = [row for row in events if row["event"]["type"] == "response.function_call_arguments.delta"]
        text = [row for row in events if row["event"]["type"] == "response.output_text.delta"]
        summaries = [row for row in events if row["event"]["type"] == "response.reasoning_summary_text.delta"]
        delegation_id = events[0].get("delegation_id")
        created = timestamp("response.created")
        request = next((row["event"].get("http_started_ns", row["observed_ns"]) for row in reversed(rows)
            if row["event"]["type"] == "selector.request.started" and row.get("delegation_id") == delegation_id
            and row["observed_ns"] <= events[0]["observed_ns"]), None)
        headers = next((row["event"].get("headers_received_ns", row["observed_ns"]) for row in reversed(rows)
            if row["event"]["type"] == "selector.http.headers" and row.get("delegation_id") == delegation_id
            and row["observed_ns"] <= events[0]["observed_ns"]), None)
        output = final.get("output")
        if not output:
            output = [row["event"]["item"] for row in events if row["event"]["type"] == "response.output_item.done"]
        returned_tools = next((row["returned_tools"] for row in reversed(events) if "returned_tools" in row), [])
        usage = final.get("usage") or {}
        reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        calls = []
        for item in output:
            if item.get("type") != "function_call":
                continue
            try:
                values = json.loads(item["arguments"])
            except (ValueError, KeyError):
                values = None
            requested = next((tool for tool in document["configuration"].get("requested_tools", []) if tool["name"] == item.get("name")), {})
            returned = next((tool for tool in returned_tools if tool["name"] == item.get("name")), {})
            minimum = {key: value for key, value in (values or {}).items() if key in requested.get("required", [])}
            calls.append({"name": item.get("name"), "arguments": values, "argument_characters": len(item.get("arguments", "")),
                "optional_keys_emitted": [key for key in (values or {}) if key not in requested.get("required", [])],
                "required_only_argument_characters": len(json.dumps(minimum, separators=(",", ":"), ensure_ascii=False)),
                "requested_schema": requested, "returned_schema": returned})
        first_args = arguments[0]["observed_ns"] if arguments else None
        last_args = arguments[-1]["observed_ns"] if arguments else None
        function_done = next((row["observed_ns"] for row in events if row["event"]["type"] == "response.output_item.done"
            and row["event"].get("item", {}).get("type") == "function_call"
            and (not arguments or row["event"]["item"].get("id") == arguments[-1]["event"].get("item_id"))), None)
        span = interval(last_args, first_args)
        responses.append({"id": identifier, "status": final.get("status", "unfinished_capture"), "usage": usage,
            "reasoning_tokens": reasoning,
            "non_reasoning_output_tokens": usage.get("output_tokens") - reasoning if usage.get("output_tokens") is not None and reasoning is not None else None,
            "timings_ms": {"http_start_to_headers": interval(headers, request),
                "http_start_to_first_summary_delta": interval(summaries[0]["observed_ns"] if summaries else None, request),
                "response_created_to_first_arguments_delta": interval(first_args, created),
                "http_start_to_first_arguments_delta": interval(first_args, request),
                "first_to_last_argument_delta": span,
                "last_argument_delta_to_item_done": interval(function_done, last_args),
                "first_to_last_text_delta": interval(text[-1]["observed_ns"], text[0]["observed_ns"]) if text else None},
            "argument_chunks": {"count": len(arguments), "characters": sum(len(row["event"].get("delta", "")) for row in arguments),
                "subsequent_characters_per_second": round(sum(len(row["event"].get("delta", "")) for row in arguments[1:]) / (span/1000), 2) if span else None,
                "maximum_interarrival_ms": max((interval(b["observed_ns"], a["observed_ns"]) for a, b in zip(arguments, arguments[1:])), default=None)},
            "summary_text_from_deltas": "".join(row["event"].get("delta", "") for row in summaries),
            "full_returned_output": output, "calls": calls})
    voice = [row for row in rows if row["stream"] == "voice" and row["event"]["type"] == "session.output_transcript.delta"]
    return {"session_id": document["session_id"], "configuration": document["configuration"],
        "capture_health": document["capture_health"], "voice_output_fragments": voice,
        "voice_text": "".join(row["event"].get("delta", "") for row in voice), "responses": responses,
        "limitations": ["No raw hidden reasoning or per-token IDs/logprobs are returned here.",
            "Streaming deltas are variable-sized chunks; arrival throughput is not exact provider token throughput.",
            "Reasoning summary generation changes the request and is not a raw reasoning timeline.",
            "Full returned output means all exposed output received before session close; unfinished responses remain unknown."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = []
    for path in sorted(args.directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        analysis = analyze(document)
        if analysis["responses"]:
            analysis["source"] = str(path)
            result.append(analysis)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
