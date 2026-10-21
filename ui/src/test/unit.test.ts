import { describeDetail } from "../api/client";
import { envToText, parseEnv, splitList } from "../lib/config";
import { bytesToQuantity, cpuToQuantity, formatBytes, formatCpu, parseCpu, parseQuantity, shortDigest, timeAgo } from "../lib/format";
import { describeLimit, limitsToText, parseLimits, summarizeLimits, usageTotals, type UsageDay } from "../lib/limits";
import { exceeded } from "../lib/resources";
import { coresLabel, formatCores, niceCeil, niceCeilBytes, segments, share, xTicks, yTicks } from "../lib/usage";

const R = { cpu_m: 1000, memory_bytes: 2 * 1024 ** 3, gpu: false, disk_bytes: 10 * 1024 ** 3 };

describe("format", () => {
  it("formats bytes and cpu", () => {
    expect(formatBytes(2 * 1024 ** 3)).toBe("2 GiB");
    expect(formatBytes(1536 * 1024 ** 2)).toBe("1.5 GiB");
    expect(formatCpu(500)).toBe("500m");
    expect(formatCpu(2000)).toBe("2");
    expect(formatCpu(1500)).toBe("1.5");
  });
  it("round-trips quantities the Supervisor accepts", () => {
    expect(parseQuantity(bytesToQuantity(2 * 1024 ** 3))).toBe(2 * 1024 ** 3);
    expect(parseCpu(cpuToQuantity(500))).toBe(500);
    expect(parseCpu(cpuToQuantity(2000))).toBe(2000);
    expect(parseCpu("0.5")).toBe(500);
    expect(parseCpu("abc")).toBeNull();
    expect(parseQuantity("-1")).toBeNull();
    expect(parseQuantity("2Gi")).toBe(2 * 1024 ** 3);
  });
  it("shortens digests and times", () => {
    expect(shortDigest("sha256:0123456789abcdef0123")).toBe("sha256:0123456789ab");
    expect(shortDigest(null)).toBe("—");
    expect(timeAgo(1000, 1030)).toBe("just now");
    expect(timeAgo(1000, 1000 + 3 * 3600)).toBe("3 h ago");
  });
});

describe("resources", () => {
  it("flags only values above what was approved", () => {
    expect([...exceeded({ ...R, memory_bytes: R.memory_bytes * 2 }, R)]).toEqual(["memory_bytes"]);
    expect([...exceeded({ ...R, gpu: true }, R)]).toEqual(["gpu"]);
    expect(exceeded({ ...R, cpu_m: 500 }, R).size).toBe(0);
  });
  it("flags everything on first deploy", () => {
    expect(exceeded(R, null).has("cpu_m")).toBe(true);
    expect(exceeded(R, null).has("gpu")).toBe(false);
  });
});

describe("env parsing", () => {
  it("parses KEY=VALUE lines, keeping '=' in values", () => {
    expect(parseEnv("A=1\n\nB=x=y")).toEqual({ env: { A: "1", B: "x=y" } });
    expect(envToText({ A: "1", B: "2" })).toBe("A=1\nB=2");
  });
  it("rejects bad lines", () => {
    expect(parseEnv("novalue")).toHaveProperty("error");
    expect(parseEnv("lower=1")).toHaveProperty("error");
    expect(parseEnv("A=1\nA=2")).toHaveProperty("error");
  });
  it("splits lists", () => {
    expect(splitList("a, b\nc  d")).toEqual(["a", "b", "c", "d"]);
  });
});

describe("error details", () => {
  it("handles FastAPI string and validation details", () => {
    expect(describeDetail("nope", "x")).toBe("nope");
    expect(describeDetail([{ loc: ["body", "name"], msg: "too short" }], "x")).toBe("name: too short");
    expect(describeDetail(undefined, "fallback")).toBe("fallback");
  });
});


describe("usage chart helpers", () => {
  it("rounds an axis top up to a round number", () => {
    expect(niceCeil(0)).toBe(1);
    expect(niceCeil(87)).toBe(100);
    expect(niceCeil(101)).toBe(200);
    expect(niceCeil(230)).toBe(250);
    expect(niceCeil(0.31)).toBe(0.5);
    expect(niceCeil(2 * 1024 ** 3)).toBeGreaterThanOrEqual(2 * 1024 ** 3);
  });

  it("rounds memory axes in binary units", () => {
    const MI = 1024 ** 2;
    expect(niceCeilBytes(40 * MI)).toBe(50 * MI);
    expect(niceCeilBytes(100 * MI)).toBe(100 * MI);
    expect(niceCeilBytes(1.2 * 1024 ** 3)).toBe(2 * 1024 ** 3);
    expect(coresLabel(1000)).toBe("1 core");
    expect(coresLabel(500)).toBe("500m");
    expect(coresLabel(2500)).toBe("2.5 cores");
  });

  it("builds evenly spaced ticks including both ends", () => {
    expect(yTicks(200)).toEqual([0, 50, 100, 150, 200]);
    expect(xTicks(0, 400, 5)).toEqual([0, 100, 200, 300, 400]);
  });

  it("splits a series at gaps longer than 2.5 steps", () => {
    const p = (t: number) => ({ t, cpu_m: 1, cpu_m_max: 1, memory_bytes: 1, memory_bytes_max: 1 });
    expect(segments([p(0), p(5), p(10), p(100), p(105)], 5).map((s) => s.length)).toEqual([3, 2]);
    expect(segments([], 5)).toEqual([]);
    expect(segments([p(0), p(12)], 5).length).toBe(1);
  });

  it("formats cores and shares", () => {
    expect(formatCores(250)).toBe("250m");
    expect(formatCores(1000)).toBe("1");
    expect(formatCores(1500)).toBe("1.5");
    expect(formatCores(1234.6)).toBe("1.24");
    expect(share(500, 1000)).toBe(0.5);
    expect(share(5000, 1000)).toBe(1);
    expect(share(5, null)).toBeNull();
  });
});

describe("limits", () => {
  it("leaves blank fields out so the Router default applies, and keeps 0 as unlimited", () => {
    expect(parseLimits({ rpm: "", poll_rpm: " 120 ", jobs_per_day: "0" })).toEqual({ limits: { poll_rpm: 120, jobs_per_day: 0 } });
    expect(parseLimits({})).toEqual({ limits: {} });
  });
  it("rejects what the Supervisor would refuse, naming the field", () => {
    expect(parseLimits({ rpm: "-1" })).toEqual({ error: expect.stringContaining("Requests per minute") });
    expect(parseLimits({ rpm: "1.5" })).toHaveProperty("error");
    expect(parseLimits({ rpm: "abc" })).toHaveProperty("error");
    expect(parseLimits({ rpm: "10000001" })).toHaveProperty("error");
  });
  it("describes absent, unlimited and numeric limits", () => {
    expect(describeLimit(undefined)).toBe("default");
    expect(describeLimit(0)).toBe("unlimited");
    expect(describeLimit(1500)).toBe("1,500");
  });
  it("turns stored limits into form text and a short summary", () => {
    expect(limitsToText({ rpm: 30 })).toEqual({ rpm: "30", poll_rpm: "", jobs_per_day: "", max_outstanding_jobs: "" });
    expect(summarizeLimits({})).toBe("defaults");
    expect(summarizeLimits({ rpm: 30, jobs_per_day: 0 })).toBe("30 req/min · unlimited jobs/day");
  });
  it("adds up a key's usage per model across days", () => {
    const days: UsageDay[] = [
      { day: "20261021", models: { xray: { requests: 5, errors: 1, limited: 0 } } },
      { day: "20261020", models: { xray: { requests: 3, errors: 0, limited: 2 }, ecg: { requests: 1, errors: 0, limited: 0 } } },
    ];
    expect(usageTotals(days)).toEqual([
      { model: "xray", requests: 8, errors: 1, limited: 2 },
      { model: "ecg", requests: 1, errors: 0, limited: 0 },
    ]);
  });
});
