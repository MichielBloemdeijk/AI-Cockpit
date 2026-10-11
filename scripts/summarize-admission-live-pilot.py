"""Summarize diagnostic admission pilots using intervals within each clock domain."""
import argparse
import json
import sqlite3
from pathlib import Path


def difference(end, start):
    return None if end is None or start is None else round((end - start) / 1_000_000, 4)


def summarize(folder):
    report = json.loads((folder / "audio/acceptance.json").read_text(encoding="utf-8"))
    check = next(row for row in report["checks"] if row["id"] == "ready_latency")
    samples = []
    for sample in check["evidence"]["samples"]:
        raw = sample["raw_observations"]
        def event(name):
            return next((row for row in raw if row["event"] == name), {})
        stages = sample.get("server_stages", {}).get("observations", [])
        arguments = next((row for row in stages if row["stage"] == "tool_arguments_complete"), {})
        origin = arguments.get("origin_correlation_id")
        def server(name):
            return next((row for row in stages if row["stage"] == name and row.get("origin_correlation_id") == origin), {})
        def android(name):
            return next((row for row in raw if row["event"] == "live_stage_received" and row.get("stage") == name
                and row.get("origin_correlation_id") == origin), {})
        receipt = event("durable_admission")
        client = lambda name: android(name).get("client_stream_received_ns")
        last_sample = event("command_last_input_sample").get("command_last_sample_ns")
        stamp = lambda name: server(name).get("server_monotonic_ns")
        delivery = receipt.get("server_delivery_timing", {})
        capture = next((row for row in raw if row["event"] == "capture_requested" and row.get("mode") == "fresh"), {})
        capture_id = capture.get("capture_id")
        def image(name):
            return next((row for row in raw if row["event"] == name and row.get("capture_id") == capture_id), {})
        samples.append({"case": sample["case_id"], "status": sample["status"],
            "android_intervals_ms": {
                "speech_end_to_delegation_relay": difference(client("delegation_received"), last_sample),
                "delegation_relay_to_response_created_relay": difference(client("selector_response_started"), client("delegation_received")),
                "delegation_relay_to_validated_tool_relay": difference(client("tool_arguments_complete"), client("delegation_received")),
                "validated_tool_relay_to_admission_receipt": difference(receipt.get("client_received_ns"), client("tool_arguments_complete")),
                "speech_end_to_admission_receipt": receipt.get("latency_ms"),
                "fresh_request_to_callback": difference(image("capture_callback").get("callback_ns"), capture.get("observed_ns")),
                "fresh_callback_to_encoding_finished": difference(image("capture_encoding_finished").get("observed_ns"), image("capture_callback").get("callback_ns")),
                "client_dispatch": receipt.get("client_dispatch_ms")},
            "server_intervals_ms": {
                "delegation_to_transcript_ready": difference(stamp("transcript_ready"), stamp("delegation_received")),
                "delegation_to_response_created": difference(stamp("selector_response_started"), stamp("delegation_received")),
                "delegation_to_tool_arrival": difference(arguments.get("tool_item_received_ns"), stamp("delegation_received")),
                "managed_response_observed_to_tool_arrival": difference(arguments.get("tool_item_received_ns"), stamp("selector_response_started")),
                "tool_validation": arguments.get("tool_validation_ms"),
                "validated_tool_to_shell": difference(stamp("shell_started"), stamp("tool_arguments_complete")),
                "shell_to_authority_ready": difference(stamp("authority_ready"), stamp("shell_started")),
                "gateway_to_commit": difference(stamp("admission_commit"), stamp("gateway_started")),
                "commit_to_gateway_finished": difference(stamp("gateway_finished"), stamp("admission_commit")),
                "commit_to_sse_emitted": difference(delivery.get("sse_emitted_ns"), stamp("admission_commit")),
                "tool_arrival_to_sse_emitted": difference(delivery.get("sse_emitted_ns"), arguments.get("tool_item_received_ns")),
                "result_record_started_to_sse_emitted": difference(delivery.get("sse_emitted_ns"), delivery.get("result_record_started_ns"))}})
    with sqlite3.connect((folder / "fixture.db").resolve().as_uri() + "?mode=ro", uri=True) as db:
        events = [(kind, json.loads(raw)) for kind, raw in db.execute(
            "SELECT event_type,payload_json FROM live_session_events WHERE event_type IN "
            "('provider.usage','provider.delegation_usage','provider.admission_stage') ORDER BY sequence")]
        completed = [value for kind, value in events if kind == "provider.delegation_usage"]
        response_starts = [value for kind, value in events if kind == "provider.admission_stage" and value.get("stage") == "selector_response_started"]
        usages = [{"response_id": value.get("response_id"), "tier": value.get("actual_tier"), "usage": value.get("usage")}
            for value in completed]
        usage = {"completed_selector_responses": len(completed), "observed_managed_response_starts": len(response_starts),
            "selector_input_tokens": sum(value.get("usage", {}).get("input_tokens", 0) for value in completed),
            "selector_output_tokens": sum(value.get("usage", {}).get("output_tokens", 0) for value in completed),
            "reported_live_seconds": sum(value.get("usage", {}).get("seconds", 0) for kind, value in events if kind == "provider.usage"),
            "responses": usages, "cost": None, "cost_status": "unavailable",
            "incomplete_requests_usage": "unknown", "durable_tasks": db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            "image_upload_records": db.execute("SELECT COUNT(*) FROM task_context_requests").fetchone()[0]}
    return {"source": str(folder / "audio/acceptance.json"), "candidate": report["candidate"]["id"],
        "acceptance_claim": False, "samples": samples, "usage": usage}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="append", required=True, help="LABEL=folder")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {label: summarize(Path(path)) for label, path in (item.split("=", 1) for item in args.pilot)}
    result["method"] = "Single-sample diagnostics, no p95 claim. Relay intervals include event delivery; clocks are never subtracted across domains."
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
