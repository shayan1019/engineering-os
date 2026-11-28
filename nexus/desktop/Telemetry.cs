using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace Eos.Nexus
{
    internal static class ProcessMemory
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct Counters
        {
            internal uint Size, PageFaults;
            internal UIntPtr PeakWorkingSet, WorkingSet, PeakPagedPool, PagedPool,
                PeakNonPagedPool, NonPagedPool, Pagefile, PeakPagefile;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool K32GetProcessMemoryInfo(IntPtr process, ref Counters counters, uint size);

        private static readonly uint Size = (uint)Marshal.SizeOf(typeof(Counters));

        internal static long WorkingSet(Process process)
        {
            var counters = new Counters { Size = Size };
            return K32GetProcessMemoryInfo(process.Handle, ref counters, Size)
                ? (long)counters.WorkingSet.ToUInt64() : 0;
        }
    }

    internal static class Wire
    {
        internal const int MaxLine = 2 * 1024 * 1024;

        internal static JavaScriptSerializer Json()
        {
            return new JavaScriptSerializer { MaxJsonLength = MaxLine, RecursionLimit = 16 };
        }

        internal static Dictionary<string, object> Map(object raw)
        {
            return raw as Dictionary<string, object> ?? new Dictionary<string, object>();
        }

        internal static object Get(Dictionary<string, object> value, string key)
        {
            object found;
            return value.TryGetValue(key, out found) ? found : null;
        }

        internal static string String(Dictionary<string, object> value, string key)
        {
            var text = Get(value, key) as string;
            return text == null ? "" : text.Substring(0, Math.Min(text.Length, 8192));
        }

        internal static bool Bool(Dictionary<string, object> value, string key)
        {
            return Object.Equals(Get(value, key), true);
        }

        internal static IEnumerable<object> Array(Dictionary<string, object> value, string key)
        {
            var list = Get(value, key) as IList;
            return list == null ? Enumerable.Empty<object>() : list.Cast<object>();
        }

        internal static DateTime Time(Dictionary<string, object> value, string key)
        {
            DateTime result;
            return DateTime.TryParse(String(value, key), CultureInfo.InvariantCulture,
                DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out result)
                ? result : DateTime.MinValue;
        }

        internal static void WriteAtomic(string path, string contents)
        {
            string absolute = Path.GetFullPath(path);
            Directory.CreateDirectory(Path.GetDirectoryName(absolute));
            string staging = absolute + "." + Guid.NewGuid().ToString("n") + ".tmp";
            try
            {
                File.WriteAllText(staging, contents, new UTF8Encoding(false));
                if (File.Exists(absolute)) File.Replace(staging, absolute, null);
                else File.Move(staging, absolute);
            }
            finally { if (File.Exists(staging)) File.Delete(staging); }
        }
    }

    internal sealed class Observation
    {
        private static readonly string[] FieldNames = { "id", "ts", "project", "session", "host", "family", "kind", "status",
            "verdict", "agent", "parent", "skill", "tool", "provider", "model", "server", "query", "task", "correlation",
            "mechanism", "version", "severity", "evidence", "source", "artifact_sha256", "connection_status", "duration_seconds", "input_bytes",
            "output_bytes", "argument_count", "input_tokens", "output_tokens", "cost", "currency" };
        internal readonly Dictionary<string, object> Fields;
        internal readonly string Id, Project, Session, Host, Family, Kind, Status, Verdict;
        internal readonly string Agent, Skill, Parent, Tool, Correlation;
        internal readonly DateTime Timestamp;

        internal Observation(object raw)
        {
            var record = Wire.Map(raw);
            Fields = FieldNames.Where(record.ContainsKey).ToDictionary(x => x, x => record[x], StringComparer.Ordinal);
            Id = Read("id"); Project = Read("project"); Session = Read("session"); Host = Read("host");
            Family = Read("family"); Kind = Read("kind"); Status = Read("status"); Verdict = Read("verdict");
            Agent = Read("agent"); Skill = Read("skill"); Parent = Read("parent"); Tool = Read("tool");
            Correlation = Read("correlation"); Timestamp = Wire.Time(Fields, "ts");
        }

        internal string Read(string key) { return Wire.String(Fields, key); }

        internal bool IsSecurity
        {
            get {
                return new[] { "security", "guardian", "permissions", "edr", "policy", "secret_scan" }
                    .Contains(Family.ToLowerInvariant()) || Verdict == "DENY" || Status == "DENIED";
            }
        }

        internal string Subject
        {
            get {
                if (Skill.Length > 0) return Skill;
                if (Tool.Length > 0) return Tool;
                if (Agent.Length > 0) return Agent;
                if (Read("server").Length > 0) return Read("server");
                return Kind.Length > 0 ? Kind : Family;
            }
        }
    }

    internal sealed class SessionInfo
    {
        internal readonly string Id, Project, Host, Mode;
        internal readonly DateTime LastSeen;

        internal SessionInfo(object raw)
        {
            var fields = Wire.Map(raw);
            Id = Wire.String(fields, "id"); Project = Wire.String(fields, "project");
            Host = Wire.String(fields, "host"); Mode = Wire.String(fields, "mode");
            LastSeen = Wire.Time(fields, "lastSeen");
        }

        internal string Key { get { return Project + "\0" + Host + "\0" + Id; } }

        internal bool Contains(Observation item)
        {
            return item.Project == Project && item.Session == Id && item.Host == Host;
        }
    }

    internal sealed class Snapshot
    {
        internal readonly string Mode;
        internal readonly List<SessionInfo> Sessions;
        internal readonly List<Observation> Events;
        internal readonly Dictionary<string, object> Registries;
        internal readonly Dictionary<string, string> Projects;
        internal readonly Dictionary<string, Observation> Graphify;
        internal readonly List<string> Issues;
        internal readonly Dictionary<string, object> IssueCounts;
        internal readonly DateTime Received;
        internal static readonly Snapshot Empty = new Snapshot(new Dictionary<string, object>());

        internal Snapshot(Dictionary<string, object> value)
        {
            Mode = Wire.String(value, "mode");
            if (Mode != "live" && Mode != "stale" && Mode != "demo") Mode = "disconnected";
            Received = DateTime.UtcNow;
            Registries = Wire.Map(Wire.Get(value, "registries"));
            Sessions = Wire.Array(value, "sessions").Take(512).Select(x => new SessionInfo(x))
                .Where(x => x.Id.Length != 0 && x.Project.Length != 0)
                .GroupBy(x => x.Key).Select(x => x.First()).ToList();
            Events = Wire.Array(value, "events").Take(500).Select(x => new Observation(x))
                .Where(x => x.Id.Length > 0 && x.Session.Length > 0 && x.Project.Length > 0
                    && x.Timestamp != DateTime.MinValue)
                .GroupBy(x => x.Project + "\0" + x.Host + "\0" + x.Session + "\0" + x.Id)
                .Select(x => x.First()).OrderBy(x => x.Timestamp).ToList();
            Projects = new Dictionary<string, string>(StringComparer.Ordinal);
            Graphify = new Dictionary<string, Observation>(StringComparer.Ordinal);
            foreach (var project in Wire.Array(value, "projects").Take(512).Select(Wire.Map))
            {
                string id = Wire.String(project, "id");
                if (id.Length > 0) Projects[id] = Wire.String(project, "label");
                var build = new Observation(Wire.Get(project, "graphify"));
                if (id.Length > 0 && build.Project == id && build.Id.Length > 0 && build.Kind == "graph.reported"
                    && build.Read("source") == "graphify-build-state" && build.Timestamp != DateTime.MinValue)
                    Graphify[id] = build;
            }
            Issues = Wire.Array(value, "issues").OfType<string>().Take(24).ToList();
            IssueCounts = Wire.Map(Wire.Get(value, "issueCounts"));
        }

        internal IEnumerable<string> Items(string name)
        {
            return Wire.Array(Wire.Map(Wire.Get(Registries, name)), "items").OfType<string>()
                .Where(x => !String.IsNullOrWhiteSpace(x) && x.Length < 128).Take(512);
        }

        internal IEnumerable<string> AdapterOnly(string name)
        {
            return Wire.Array(Wire.Map(Wire.Get(Registries, name)), "adapterOnly").OfType<string>().Take(96);
        }

        internal static Snapshot Demo()
        {
            DateTime now = DateTime.UtcNow;
            var data = new Dictionary<string, object> {
                { "mode", "demo" }, { "projects", new [] {
                    new Dictionary<string, object> { { "id", "demo" }, { "label", "NEXUS demo" } } } },
                { "sessions", new [] { new Dictionary<string, object> { { "id", "demo" }, { "project", "demo" },
                    { "host", "demo" }, { "mode", "demo" }, { "lastSeen", now.ToString("o") } } } },
                { "registries", new Dictionary<string, object> {
                    { "AGENT_REGISTRY", new Dictionary<string, object> { { "items",
                        new [] { "chief-architect", "architect", "implementer", "test-engineer", "security-auditor",
                            "reviewer", "evidence-analyst", "performance-engineer" } } } },
                    { "SKILL_INDEX", new Dictionary<string, object> { { "items",
                        new [] { "fable-5-1", "fable5-thinking", "graph-engineering" } } } } } }
            };
            data["events"] = new[] {
                DemoEvent("1", now.AddSeconds(-5), "router", "route", "SELECTED", null, null, null),
                DemoEvent("2", now.AddSeconds(-3), "skill", "skill.selected", "SELECTED", null, "fable-5-1", null),
                DemoEvent("3", now.AddSeconds(-2), "agent", "agent.started", "ACTIVE", "implementer", null, null),
                DemoEvent("4", now.AddSeconds(-1), "tool", "tool.started", "ACTIVE", null, null, "test"),
                DemoEvent("5", now, "security", "write.denied", "DENIED", null, null, null)
            };
            return new Snapshot(data);
        }

        private static Dictionary<string, object> DemoEvent(string id, DateTime time, string family, string kind,
            string status, string agent, string skill, string tool)
        {
            return new Dictionary<string, object> { { "id", id }, { "project", "demo" }, { "session", "demo" },
                { "host", "demo" }, { "ts", time.ToString("o") }, { "family", family }, { "kind", kind },
                { "status", status }, { "agent", agent }, { "skill", skill }, { "tool", tool }, { "source", "demo" } };
        }
    }

    internal sealed class ObserverConnection : IDisposable
    {
        private readonly LaunchOptions options;
        private readonly string[] workspaces;
        private readonly bool lowResource;
        private readonly Action<Snapshot> receive;
        private readonly object sync = new object();
        private Process child;
        private volatile bool stopped;

        internal ObserverConnection(LaunchOptions options, IEnumerable<string> workspaces, bool lowResource,
            Action<Snapshot> receive)
        {
            this.options = options;
            this.workspaces = workspaces.Take(32).ToArray();
            this.lowResource = lowResource;
            this.receive = receive;
        }

        internal void Start()
        {
            var worker = new Thread(Follow) { IsBackground = true, Name = "NEXUS observer connection" };
            worker.Start();
        }

        private void Follow()
        {
            while (!stopped)
            {
                if (File.Exists(options.Python) && File.Exists(options.Observer))
                {
                    try { ReadSnapshots(); }
                    catch (Exception error)
                    {
                        if (!(error is IOException || error is UnauthorizedAccessException ||
                            error is ArgumentException || error is InvalidOperationException ||
                            error is System.ComponentModel.Win32Exception)) throw;
                    }
                }
                if (!stopped) receive(null);
                for (int tick = 0; tick < 25 && !stopped; tick++) Thread.Sleep(200);
            }
        }

        private void ReadSnapshots()
        {
            var args = new List<string> { "-I", "-B", "-u", options.Observer, "--stdio", "--engine", options.Engine,
                "--eos-home", options.Home, "--interval", lowResource ? "1.5" : "0.5" };
            foreach (string path in workspaces) { args.Add("--workspace"); args.Add(path); }
            var start = new ProcessStartInfo(options.Python, String.Join(" ", args.Select(Identity.Quote)))
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, WorkingDirectory = Path.GetDirectoryName(options.Observer)
            };
            byte[] token = new byte[32];
            using (var random = RandomNumberGenerator.Create()) random.GetBytes(token);
            string proof;
            using (var hmac = new HMACSHA256(token))
                proof = Identity.Hex(hmac.ComputeHash(Encoding.UTF8.GetBytes("eos-nexus-stdio-v1")));

            using (var process = new Process { StartInfo = start })
            {
                try
                {
                    lock (sync)
                    {
                        if (stopped) return;
                        process.Start();
                        child = process;
                    }
                    process.ErrorDataReceived += (sender, error) => { };
                    process.BeginErrorReadLine();
                    process.StandardInput.WriteLine("{\"type\":\"hello\",\"token\":\"" + Identity.Hex(token) + "\"}");
                    process.StandardInput.Flush();
                    var input = process.StandardOutput;
                    var pending = input.ReadLineAsync();
                    if (!pending.Wait(8000) || pending.Result == null || pending.Result.Length > 1024) return;
                    var greeting = Wire.Map(Wire.Json().DeserializeObject(pending.Result));
                    if (Wire.String(greeting, "type") != "hello" || !Object.Equals(Wire.Get(greeting, "schema"), 1)
                        || !FixedEquals(Wire.String(greeting, "proof"), proof)) return;
                    while (!stopped)
                    {
                        var next = input.ReadLineAsync();
                        if (!next.Wait(18000)) return;
                        string line = next.Result;
                        if (line == null || line.Length > Wire.MaxLine) return;
                        var message = Wire.Map(Wire.Json().DeserializeObject(line));
                        if (Wire.String(message, "type") == "snapshot" && Object.Equals(Wire.Get(message, "schema"), 1))
                            receive(new Snapshot(message));
                    }
                }
                catch (AggregateException error)
                {
                    if (!(error.GetBaseException() is IOException ||
                        error.GetBaseException() is ObjectDisposedException)) throw;
                }
                finally
                {
                    lock (sync)
                    {
                        if (child == process) { StopChild(process); child = null; }
                    }
                }
            }
        }

        private static bool FixedEquals(string left, string right)
        {
            if (left.Length != right.Length) return false;
            int mismatch = 0;
            for (int n = 0; n < right.Length; n++) mismatch |= left[n] ^ right[n];
            return mismatch == 0;
        }

        private static void StopChild(Process process)
        {
            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
        }

        internal KeyValuePair<double, long> Metrics()
        {
            lock (sync)
            {
                if (child == null) return new KeyValuePair<double, long>();
                try
                {
                    return new KeyValuePair<double, long>(child.TotalProcessorTime.TotalSeconds, ProcessMemory.WorkingSet(child));
                }
                catch (InvalidOperationException) { return new KeyValuePair<double, long>(); }
                catch (System.ComponentModel.Win32Exception) { return new KeyValuePair<double, long>(); }
            }
        }

        public void Dispose()
        {
            stopped = true;
            lock (sync) if (child != null) StopChild(child);
        }
    }

    internal sealed class SessionArchive
    {
        private readonly string root;
        private const int MaxSessions = 64;
        private readonly Dictionary<string, List<Observation>> events = new Dictionary<string, List<Observation>>();
        private readonly HashSet<string> dirty = new HashSet<string>();
        private readonly Dictionary<string, KeyValuePair<DateTime, Observation>> headers =
            new Dictionary<string, KeyValuePair<DateTime, Observation>>();
        private DateTime lastSave = DateTime.MinValue;

        internal SessionArchive(string home)
        {
            root = Path.Combine(home, "nexus", "archive");
        }

        internal void Store(Snapshot snapshot, int days)
        {
            if (days <= 0 || snapshot.Mode == "demo") return;
            var cutoff = DateTime.UtcNow.AddDays(-days);
            foreach (var info in snapshot.Sessions.OrderByDescending(x => x.LastSeen).Take(MaxSessions))
            {
                var incoming = snapshot.Events.Where(x => info.Contains(x) && x.Timestamp >= cutoff).ToList();
                if (incoming.Count == 0) continue;
                List<Observation> previous;
                if (!events.TryGetValue(info.Key, out previous)) previous = Load(info, days);
                var known = new HashSet<string>(previous.Select(x => x.Id));
                if (incoming.All(x => known.Contains(x.Id))) continue;
                events[info.Key] = previous.Concat(incoming).Where(x => x.Timestamp >= cutoff)
                    .GroupBy(x => x.Id).Select(x => x.Last()).OrderBy(x => x.Timestamp)
                    .Reverse().Take(500).Reverse().ToList();
                dirty.Add(info.Key);
            }
            int remaining = 4000;
            foreach (var pair in events.OrderByDescending(x => x.Value.Last().Timestamp).ToArray())
            {
                remaining -= pair.Value.Count;
                if (remaining <= 0 && !dirty.Contains(pair.Key)) events.Remove(pair.Key);
            }
        }

        internal List<Observation> Load(SessionInfo info, int days)
        {
            if (days <= 0) return new List<Observation>();
            List<Observation> list;
            if (!events.TryGetValue(info.Key, out list))
            {
                list = Read(ArchivePath(info.Key)).Where(info.Contains).ToList();
            }
            return list.Where(x => x.Timestamp >= DateTime.UtcNow.AddDays(-days)).ToList();
        }

        internal IEnumerable<SessionInfo> Sessions(int days)
        {
            if (days <= 0 || !Directory.Exists(root)) yield break;
            string[] files;
            try { files = Directory.GetFiles(root, "*.json"); }
            catch (IOException) { yield break; }
            catch (UnauthorizedAccessException) { yield break; }
            foreach (string file in files.Take(MaxSessions))
            {
                Observation entry;
                try
                {
                    DateTime modified = File.GetLastWriteTimeUtc(file);
                    KeyValuePair<DateTime, Observation> cached;
                    if (!headers.TryGetValue(file, out cached) || cached.Key != modified)
                    {
                        entry = Read(file).LastOrDefault();
                        headers[file] = new KeyValuePair<DateTime, Observation>(modified, entry);
                    }
                    else entry = cached.Value;
                }
                catch (IOException) { continue; }
                catch (UnauthorizedAccessException) { continue; }
                if (entry == null || entry.Timestamp < DateTime.UtcNow.AddDays(-days)) continue;
                yield return new SessionInfo(new Dictionary<string, object> {
                    { "id", entry.Session }, { "project", entry.Project }, { "host", entry.Host },
                    { "mode", "stale" }, { "lastSeen", entry.Timestamp.ToString("o") } });
            }
        }

        internal void Flush(int days, bool force)
        {
            if (!force && DateTime.UtcNow - lastSave < TimeSpan.FromSeconds(15)) return;
            lastSave = DateTime.UtcNow;
            try
            {
                if (days > 0)
                    foreach (var key in dirty.ToArray())
                    {
                        var archive = events[key].Where(x => x.Timestamp >= DateTime.UtcNow.AddDays(-days));
                        Wire.WriteAtomic(ArchivePath(key), Wire.Json().Serialize(archive.Select(x => x.Fields)));
                        dirty.Remove(key);
                    }
                else
                {
                    dirty.Clear();
                    events.Clear();
                    headers.Clear();
                }
                if (!Directory.Exists(root)) return;
                var cutoff = DateTime.UtcNow.AddDays(-days);
                var owned = new DirectoryInfo(root).GetFiles("*.json")
                    .Where(x => x.Name.Length == 69 && x.Name.Substring(0, 64).All(Uri.IsHexDigit))
                    .OrderByDescending(x => x.LastWriteTimeUtc).ToList();
                foreach (var file in owned.Where((x, index) => days == 0 || index >= MaxSessions
                    || x.LastWriteTimeUtc < cutoff))
                {
                    file.Delete();
                    headers.Remove(file.FullName);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (InvalidOperationException) { }
        }

        private string ArchivePath(string key) { return Path.Combine(root, Identity.Hash(key) + ".json"); }

        private static IEnumerable<Observation> Read(string file)
        {
            try
            {
                var info = new FileInfo(file);
                if (!info.Exists || info.Length > Wire.MaxLine || (info.Attributes & FileAttributes.ReparsePoint) != 0)
                    return Enumerable.Empty<Observation>();
                var items = Wire.Json().DeserializeObject(File.ReadAllText(file)) as IList;
                if (items != null) return items.Cast<object>().Take(500).Select(x => new Observation(x))
                    .Where(x => x.Id.Length > 0 && x.Session.Length > 0 && x.Project.Length > 0
                        && x.Timestamp != DateTime.MinValue).ToList();
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (InvalidOperationException) { }
            catch (ArgumentException) { }
            return Enumerable.Empty<Observation>();
        }
    }
}
