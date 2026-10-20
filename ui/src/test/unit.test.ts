import { describeDetail } from "../api/client";
import { envToText, parseEnv, splitList } from "../lib/config";
import { bytesToQuantity, cpuToQuantity, formatBytes, formatCpu, parseCpu, parseQuantity, shortDigest, timeAgo } from "../lib/format";
import { exceeded } from "../lib/resources";

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
