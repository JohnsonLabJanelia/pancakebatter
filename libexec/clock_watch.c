// clock_watch: once a second, check that the kernel clock works on each CPU of a chosen set.
//
// Why (2026-11-11): after 32.64 days of uptime, pancake0's chiplet 3 (CPUs 24-31, 56-63) began
// intermittently reading its time-stamp counter 2^48 x 36 cycles behind the other chiplets. Every
// kernel clock on those CPUs then froze between scheduler ticks (a 4 ms staircase), and tsc=reliable
// meant the kernel logged nothing. See docs/time_and_clocks.md.
//
// Each second, on the reference CPU and then on each --probe CPU (one per L3 group), it reads
// CLOCK_MONOTONIC_RAW around one rdtscp and then 64 more times. A CPU is bad when
//   bit 1: at least 60 of the 64 reads repeat the previous value (the clock is frozen), or
//   bit 2: its clock and its counter disagree with the reference CPU's by more than 1 ms.
// When any probed CPU is bad, or one was bad the second before, it checks every --sweep CPU too.
// It never pins itself to a CPU outside --probe/--sweep; give it only non-isolated CPUs.
//
// Writes:
//   --status FILE  rewritten every second (write to FILE.tmp, then rename); meant for /run
//   --log FILE     one JSON line per CPU state change, plus a summary each minute; at 64 MiB it is
//                  renamed to FILE.1 (replacing the old one) and a new file is started
//
// Build: cc -O2 -Wall -o clock_watch clock_watch.c   (install_clock_watch.sh does this)
// Usage: clock_watch --ref CPU --probe LIST --sweep LIST --status FILE --log FILE [--seconds N]
//        LIST is comma separated CPUs and ranges (0,3-5). --seconds N stops after N seconds (tests).
#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <x86intrin.h>

#define MAXCPU 1024
#define READS 64
#define FROZEN_MIN 60
#define OFFSET_MAX_NS 1000000.0
#define SANDWICH_MAX_NS 50000
#define LOG_MAX_BYTES (64L << 20)

static long long clk(clockid_t id) {
    struct timespec t;
    clock_gettime(id, &t);
    return t.tv_sec * 1000000000LL + t.tv_nsec;
}

static int pin(int cpu) {
    cpu_set_t s;
    CPU_ZERO(&s);
    CPU_SET(cpu, &s);
    return sched_setaffinity(0, sizeof s, &s);
}

static int parse_list(const char *text, unsigned char *set) {
    int n = 0;
    const char *p = text;
    while (*p) {
        char *end;
        long lo = strtol(p, &end, 10), hi = lo;
        if (end == p) return -1;
        if (*end == '-') {
            p = end + 1;
            hi = strtol(p, &end, 10);
            if (end == p) return -1;
        }
        if (lo < 0 || hi >= MAXCPU || hi < lo) return -1;
        for (long c = lo; c <= hi; c++) if (!set[c]) { set[c] = 1; n++; }
        if (*end == ',') end++;
        else if (*end) return -1;
        p = end;
    }
    return n;
}

// One clock reading taken right around one counter reading, retried if something got in between.
struct reading { long long clock_ns; unsigned long long tsc; int frozen; };

static struct reading measure(void) {
    struct reading r = {0};
    unsigned aux;
    for (int tries = 0; tries < 3; tries++) {
        long long a = clk(CLOCK_MONOTONIC_RAW);
        r.tsc = __rdtscp(&aux);
        long long b = clk(CLOCK_MONOTONIC_RAW);
        r.clock_ns = a + (b - a) / 2;
        if (b - a < SANDWICH_MAX_NS) break;
    }
    long long prev = clk(CLOCK_MONOTONIC_RAW), q;
    for (int k = 0; k < READS; k++) {
        q = clk(CLOCK_MONOTONIC_RAW);
        if (q == prev) r.frozen++;
        prev = q;
    }
    return r;
}

static void list_json(FILE *f, const unsigned char *set) {
    int first = 1;
    fputc('[', f);
    for (int c = 0; c < MAXCPU; c++) if (set[c]) { fprintf(f, first ? "%d" : ",%d", c); first = 0; }
    fputc(']', f);
}

static void usage(void) {
    fprintf(stderr, "usage: clock_watch --ref CPU --probe LIST --sweep LIST --status FILE --log FILE [--seconds N]\n");
    exit(2);
}

int main(int argc, char **argv) {
    static unsigned char probe[MAXCPU], sweep[MAXCPU], state[MAXCPU], checked[MAXCPU], bad_now[MAXCPU],
        last_bad_cpus[MAXCPU], minute_bad_cpus[MAXCPU];
    int ref = -1;
    long seconds = -1;
    const char *status_path = NULL, *log_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (i + 1 >= argc) usage();
        if (!strcmp(argv[i], "--ref")) ref = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--probe")) { if (parse_list(argv[++i], probe) <= 0) usage(); }
        else if (!strcmp(argv[i], "--sweep")) { if (parse_list(argv[++i], sweep) <= 0) usage(); }
        else if (!strcmp(argv[i], "--status")) status_path = argv[++i];
        else if (!strcmp(argv[i], "--log")) log_path = argv[++i];
        else if (!strcmp(argv[i], "--seconds")) seconds = atol(argv[++i]);
        else usage();
    }
    if (ref < 0 || ref >= MAXCPU || !status_path || !log_path) usage();
    for (int c = 0; c < MAXCPU; c++) if (probe[c]) sweep[c] = 1;

    // Counter rate, measured on the reference CPU over half a second. A nonsense rate means the
    // reference CPU itself misbehaved during calibration: exit and let systemd restart us.
    if (pin(ref)) { perror("pin ref"); return 1; }
    long long c0 = clk(CLOCK_MONOTONIC_RAW);
    unsigned aux;
    unsigned long long t0 = __rdtscp(&aux);
    struct timespec half = {0, 500000000};
    nanosleep(&half, NULL);
    long long c1 = clk(CLOCK_MONOTONIC_RAW);
    unsigned long long t1 = __rdtscp(&aux);
    double hz = (double)(long long)(t1 - t0) / ((c1 - c0) / 1e9);
    if (!(hz > 1e8 && hz < 1e10)) { fprintf(stderr, "calibration gave %.0f Hz; giving up\n", hz); return 1; }

    char tmp_path[4096];
    snprintf(tmp_path, sizeof tmp_path, "%s.tmp", status_path);
    double started = clk(CLOCK_REALTIME) / 1e9, last_bad = 0;
    long samples = 0, bad_seconds = 0, minute_bad_seconds = 0, swept_seconds = 0;
    int prev_any_bad = 0;
    FILE *log = fopen(log_path, "a");
    if (!log) { perror(log_path); return 1; }
    fprintf(log, "{\"t\":%.3f,\"event\":\"start\",\"ref\":%d,\"tsc_mhz\":%.3f,\"probe\":", started, ref, hz / 1e6);
    list_json(log, probe);
    fputs(",\"sweep\":", log);
    list_json(log, sweep);
    fputs("}\n", log);
    fflush(log);

    for (;;) {
        pin(ref);
        struct reading rr = measure();
        memset(checked, 0, sizeof checked);
        memset(bad_now, 0, sizeof bad_now);
        int any_bad = 0;
        for (int pass = 0; pass < 2; pass++) {
            if (pass == 1 && !any_bad && !prev_any_bad) break;
            if (pass == 1) swept_seconds++;
            for (int c = 0; c < MAXCPU; c++) {
                if (!(pass == 0 ? probe[c] : sweep[c]) || checked[c]) continue;
                checked[c] = 1;
                if (pin(c)) continue;  // offline CPU
                struct reading r = c == ref ? rr : measure();
                double pred = rr.clock_ns + (double)(long long)(r.tsc - rr.tsc) / hz * 1e9;
                double off_ns = r.clock_ns - pred;
                int st = (r.frozen >= FROZEN_MIN ? 1 : 0) | (off_ns > OFFSET_MAX_NS || off_ns < -OFFSET_MAX_NS ? 2 : 0);
                if (st) { bad_now[c] = 1; any_bad = 1; }
                if (st != state[c]) {
                    fprintf(log, "{\"t\":%.3f,\"cpu\":%d,\"state\":%d,\"was\":%d,\"frozen_reads\":%d,\"tsc\":%llu,"
                                 "\"ref_tsc\":%llu,\"clock_minus_counter_ms\":%.3f}\n",
                            clk(CLOCK_REALTIME) / 1e9, c, st, state[c], r.frozen, r.tsc, rr.tsc, off_ns / 1e6);
                    state[c] = st;
                }
            }
        }
        pin(ref);
        double now = clk(CLOCK_REALTIME) / 1e9;
        samples++;
        if (any_bad) {
            bad_seconds++;
            minute_bad_seconds++;
            last_bad = now;
            memcpy(last_bad_cpus, bad_now, sizeof bad_now);
            for (int c = 0; c < MAXCPU; c++) if (bad_now[c]) minute_bad_cpus[c] = 1;
        }
        prev_any_bad = any_bad;

        FILE *st = fopen(tmp_path, "w");
        if (st) {
            fprintf(st, "{\"version\":1,\"t\":%.3f,\"started\":%.3f,\"ref\":%d,\"tsc_mhz\":%.3f,\"samples\":%ld,"
                        "\"bad_seconds\":%ld,\"swept_seconds\":%ld,\"probe\":", now, started, ref, hz / 1e6, samples,
                    bad_seconds, swept_seconds);
            list_json(st, probe);
            fputs(",\"sweep\":", st);
            list_json(st, sweep);
            fputs(",\"bad_now\":", st);
            list_json(st, bad_now);
            if (last_bad > 0) {
                fprintf(st, ",\"last_bad\":%.3f,\"last_bad_cpus\":", last_bad);
                list_json(st, last_bad_cpus);
            } else {
                fputs(",\"last_bad\":null,\"last_bad_cpus\":[]", st);
            }
            fputs("}\n", st);
            if (fclose(st) == 0) rename(tmp_path, status_path);
        }

        if (samples % 60 == 0) {
            fprintf(log, "{\"t\":%.3f,\"event\":\"minute\",\"bad_seconds\":%ld,\"bad_cpus\":", now, minute_bad_seconds);
            list_json(log, minute_bad_cpus);
            fputs("}\n", log);
            minute_bad_seconds = 0;
            memset(minute_bad_cpus, 0, sizeof minute_bad_cpus);
        }
        fflush(log);
        if (ftell(log) > LOG_MAX_BYTES) {
            fclose(log);
            char old[4096];
            snprintf(old, sizeof old, "%s.1", log_path);
            rename(log_path, old);
            log = fopen(log_path, "a");
            if (!log) { perror(log_path); return 1; }
        }
        if (seconds > 0 && samples >= seconds) break;
        struct timespec w = {1, 0};
        nanosleep(&w, NULL);
    }
    fclose(log);
    return 0;
}
