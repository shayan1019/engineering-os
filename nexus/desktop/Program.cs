using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

[assembly: AssemblyTitle("EOS NEXUS")]
[assembly: AssemblyDescription("Read-only Engineering OS desktop mission control")]
[assembly: AssemblyVersion("1.0.0.0")]

namespace Eos.Nexus
{
    internal static class Program
    {
        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        private static int Main(string[] args)
        {
            LaunchOptions options;
            try { options = LaunchOptions.Parse(args); }
            catch (ArgumentException) { return 2; }

            var settings = Preferences.Load(options.Home);
            if (options.Automatic && !settings.AutoOpen) return 0;
            if (options.AtStartup && !settings.StartWithWindows) return 0;
            var user = WindowsIdentity.GetCurrent().User;
            string name = "EOS.NEXUS." + Identity.Hash(user.Value).Substring(0, 24)
                + "." + Process.GetCurrentProcess().SessionId;

            using (var mutex = new Mutex(false, @"Local\" + name))
            {
                bool owner;
                try { owner = mutex.WaitOne(0); }
                catch (AbandonedMutexException) { owner = true; }
                if (!owner) return InstanceChannel.Forward(name, options) ? 0 : 3;
                try
                {
                    if (options.Automatic && !settings.ClaimActivation(options)) return 0;
                    SetProcessDPIAware();
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    using (var channel = new InstanceChannel(name, user))
                    using (var window = new MissionControl(options, settings, channel))
                        Application.Run(window);
                }
                finally { mutex.ReleaseMutex(); }
            }
            return 0;
        }
    }

    internal static class Identity
    {
        internal static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        }

        internal static string Hash(string value)
        {
            using (var sha = SHA256.Create()) return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(value)));
        }

        internal static string Quote(string argument)
        {
            // CommandLineToArgvW rules, including backslashes immediately before a quote.
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in argument)
            {
                if (character == '\\') { slashes++; continue; }
                result.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
                result.Append(character);
                slashes = 0;
            }
            result.Append('\\', slashes * 2);
            return result.Append('"').ToString();
        }
    }

    internal sealed class LaunchOptions
    {
        internal string Home, Engine, Python, Observer, Workspace, Session, Host;
        internal string PerfOutput, Screenshot;
        internal int SmokeSeconds;
        internal bool Automatic, AtStartup, Demo;

        internal static LaunchOptions Parse(string[] arguments)
        {
            var options = new LaunchOptions();
            var values = new Dictionary<string, Action<string>>(StringComparer.Ordinal)
            {
                { "--eos-home", x => options.Home = x }, { "--engine", x => options.Engine = x },
                { "--python", x => options.Python = x }, { "--observer", x => options.Observer = x },
                { "--workspace", x => options.Workspace = x }, { "--session", x => options.Session = x },
                { "--host", x => options.Host = x }, { "--perf-output", x => options.PerfOutput = x },
                { "--screenshot", x => options.Screenshot = x },
                { "--smoke-seconds", x => options.SmokeSeconds = int.Parse(x) }
            };
            try
            {
                for (int index = 0; index < arguments.Length; index++)
                {
                    string argument = arguments[index];
                    if (argument == "--auto") options.Automatic = true;
                    else if (argument == "--startup") options.AtStartup = true;
                    else if (argument == "--demo") options.Demo = true;
                    else if (values.ContainsKey(argument) && index + 1 < arguments.Length)
                        values[argument](arguments[++index]);
                    else throw new ArgumentException("Unknown or incomplete NEXUS argument.");
                }
                options.Home = Path.GetFullPath(options.Home ?? Environment.GetEnvironmentVariable("EOS_HOME")
                    ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".eos"));
                string folder = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
                if (Path.GetFileName(folder).Equals("bin", StringComparison.OrdinalIgnoreCase))
                    folder = Directory.GetParent(folder).FullName;
                string nexus = Directory.GetParent(folder).FullName;
                string parent = Directory.GetParent(nexus).FullName;
                bool installed = Path.GetFileName(parent).Equals("engine", StringComparison.OrdinalIgnoreCase);
                string release = installed ? Directory.GetParent(parent).FullName : parent;
                options.Engine = Path.GetFullPath(options.Engine ?? (installed ? parent : Path.Combine(release, "engine")));
                options.Observer = Path.GetFullPath(options.Observer ?? Path.Combine(nexus, "observer.py"));
                options.Python = Path.GetFullPath(options.Python
                    ?? Path.Combine(release, "runtime", "python", "python.exe"));
                options.Workspace = options.Workspace == null ? null : Path.GetFullPath(options.Workspace);
                if (options.Automatic && String.IsNullOrWhiteSpace(options.Session)) throw new ArgumentException();
                if (options.SmokeSeconds < 0 || options.SmokeSeconds > 300) throw new ArgumentException();
                if ((options.Screenshot != null || options.PerfOutput != null) && options.SmokeSeconds == 0)
                    throw new ArgumentException();
                return options;
            }
            catch (FormatException) { throw new ArgumentException(); }
            catch (OverflowException) { throw new ArgumentException(); }
        }

        internal Dictionary<string, object> Handoff()
        {
            return new Dictionary<string, object> { { "workspace", Workspace }, { "session", Session },
                { "host", Host }, { "auto", Automatic }, { "startup", AtStartup } };
        }

        internal string ProjectedSession()
        {
            if (String.IsNullOrEmpty(Session)) return "";
            if (String.IsNullOrEmpty(Workspace) || String.IsNullOrEmpty(Host)) return Session;
            // Observer schema 1 namespaces session IDs by the normalized workspace and host.
            string path = Path.GetFullPath(Workspace).Replace('\\', '/').TrimEnd('/').ToLowerInvariant();
            string project = Identity.Hash(path).Substring(0, 24);
            return Identity.Hash(project + "\0" + Host + "\0" + Session).Substring(0, 24);
        }
    }

    internal sealed class Preferences
    {
        public bool AutoOpen { get; set; }
        public bool OpenMinimized { get; set; }
        public bool CloseToTray { get; set; }
        public bool Animations { get; set; }
        public bool ReducedMotion { get; set; }
        public bool LowResource { get; set; }
        public bool Quiet { get; set; }
        public bool Notifications { get; set; }
        public bool StartWithWindows { get; set; }
        public string Language { get; set; }
        public int RetentionDays { get; set; }
        public Dictionary<string, long> Activations { get; set; }
        internal string FileName;

        public Preferences()
        {
            Animations = true;
            CloseToTray = true;
            Language = "en";
            RetentionDays = 7;
            Activations = new Dictionary<string, long>();
        }

        internal static Preferences Load(string home)
        {
            string path = Path.Combine(home, "nexus", "preferences.json");
            Preferences result = null;
            try
            {
                if (File.Exists(path) && new FileInfo(path).Length <= 131072)
                    result = Wire.Json().Deserialize<Preferences>(File.ReadAllText(path));
            }
            catch (Exception error)
            {
                if (!(error is IOException || error is UnauthorizedAccessException ||
                    error is ArgumentException || error is InvalidOperationException)) throw;
            }
            result = result ?? new Preferences();
            result.Activations = result.Activations ?? new Dictionary<string, long>();
            result.RetentionDays = Math.Max(0, Math.Min(30, result.RetentionDays));
            result.FileName = path;
            return result;
        }

        internal void Save()
        {
            var values = new Dictionary<string, object>
            {
                { "autoOpen", AutoOpen }, { "openMinimized", OpenMinimized }, { "closeToTray", CloseToTray },
                { "animations", Animations }, { "reducedMotion", ReducedMotion }, { "lowResource", LowResource },
                { "quiet", Quiet }, { "notifications", Notifications }, { "startWithWindows", StartWithWindows },
                { "language", Language }, { "retentionDays", RetentionDays }, { "activations", Activations }
            };
            Wire.WriteAtomic(FileName, Wire.Json().Serialize(values));
        }

        internal bool ClaimActivation(LaunchOptions options)
        {
            if (!options.Automatic) return true;
            if (!AutoOpen || String.IsNullOrWhiteSpace(options.Session)) return false;
            string key = Identity.Hash((options.Host ?? "") + "\0" + (options.Workspace ?? "").ToUpperInvariant()
                + "\0" + options.Session);
            long now = DateTime.UtcNow.Ticks;
            Activations = Activations.Where(x => x.Value <= now && x.Value > now - TimeSpan.FromDays(1).Ticks)
                .OrderByDescending(x => x.Value).Take(511).ToDictionary(x => x.Key, x => x.Value);
            if (Activations.ContainsKey(key)) return false;
            Activations[key] = now;
            try { Save(); return true; }
            catch (Exception error)
            {
                if (!(error is IOException || error is UnauthorizedAccessException)) throw;
                Activations.Remove(key);
                return false;
            }
        }

        internal static void ApplyStartup(bool enabled, string home)
        {
            const string runKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
            using (var key = enabled ? Registry.CurrentUser.CreateSubKey(runKey) : Registry.CurrentUser.OpenSubKey(runKey, true))
            {
                if (enabled)
                {
                    if (key == null) throw new InvalidOperationException("Windows startup settings are unavailable.");
                    string launcher = Path.Combine(home, "bin", "eos.exe");
                    string command = File.Exists(launcher) ? Identity.Quote(launcher) + " nexus --startup --eos-home " + Identity.Quote(home)
                        : Identity.Quote(Application.ExecutablePath) + " --startup --eos-home " + Identity.Quote(home);
                    key.SetValue("EOS NEXUS", command);
                }
                else if (key != null) key.DeleteValue("EOS NEXUS", false);
            }
        }
    }

    internal sealed class InstanceChannel : IDisposable
    {
        private const int PipeAccessInbound = 1;
        private const int FileFlagOverlapped = 0x40000000;
        private const int PipeRejectRemoteClients = 8;

        [StructLayout(LayoutKind.Sequential)]
        private struct SecurityAttributes
        {
            internal int Length;
            internal IntPtr Descriptor;
            internal int Inherit;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafePipeHandle CreateNamedPipe(string path, int access, int mode, int instances,
            int outputSize, int inputSize, int timeout, ref SecurityAttributes attributes);

        private readonly string name;
        private readonly PipeSecurity security;
        private readonly object handoffLock = new object();
        private readonly Queue<Dictionary<string, object>> awaiting = new Queue<Dictionary<string, object>>();
        private Action<Dictionary<string, object>> receive;
        private volatile bool disposed;

        internal InstanceChannel(string name, SecurityIdentifier user)
        {
            this.name = name;
            security = new PipeSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.FullControl, AccessControlType.Allow));
            Start();
        }

        internal void Attach(Action<Dictionary<string, object>> listener)
        {
            lock (handoffLock)
            {
                receive = listener;
                while (awaiting.Count > 0) receive(awaiting.Dequeue());
            }
        }

        private void Dispatch(Dictionary<string, object> message)
        {
            lock (handoffLock)
            {
                if (receive != null) receive(message);
                else if (awaiting.Count < 8) awaiting.Enqueue(message);
            }
        }

        private NamedPipeServerStream Open()
        {
            byte[] descriptor = security.GetSecurityDescriptorBinaryForm();
            var pin = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
            try
            {
                var attributes = new SecurityAttributes { Length = Marshal.SizeOf(typeof(SecurityAttributes)),
                    Descriptor = pin.AddrOfPinnedObject() };
                var handle = CreateNamedPipe(@"\\.\pipe\" + name, PipeAccessInbound | FileFlagOverlapped,
                    PipeRejectRemoteClients, 1, 0, 8192, 0, ref attributes);
                if (handle.IsInvalid)
                {
                    int error = Marshal.GetLastWin32Error();
                    handle.Dispose();
                    throw new System.ComponentModel.Win32Exception(error);
                }
                return new NamedPipeServerStream(PipeDirection.In, true, false, handle);
            }
            finally { pin.Free(); }
        }

        private void Start()
        {
            var thread = new Thread(() =>
            {
                while (!disposed)
                {
                    try
                    {
                        using (var pipe = Open())
                        {
                            var connected = pipe.BeginWaitForConnection(null, null);
                            using (var wait = connected.AsyncWaitHandle)
                            {
                                while (!disposed && !wait.WaitOne(250)) { }
                                if (disposed) return;
                                pipe.EndWaitForConnection(connected);
                            }
                            var buffer = new byte[8193];
                            int position = 0;
                            var timeout = Stopwatch.StartNew();
                            while (position < buffer.Length && !disposed && timeout.ElapsedMilliseconds < 1000)
                            {
                                var reading = pipe.BeginRead(buffer, position, buffer.Length - position, null, null);
                                int count;
                                using (var wait = reading.AsyncWaitHandle)
                                {
                                    if (!wait.WaitOne(150)) break;
                                    count = pipe.EndRead(reading);
                                }
                                if (count == 0) break;
                                int end = Array.IndexOf(buffer, (byte)'\n', position, count);
                                position += count;
                                if (end < 0) continue;
                                if (end <= 8192)
                                {
                                    var message = Wire.Json().DeserializeObject(Encoding.UTF8.GetString(buffer, 0, end))
                                        as Dictionary<string, object>;
                                    if (message != null) Dispatch(message);
                                }
                                break;
                            }
                        }
                    }
                    catch (Exception error)
                    {
                        if (!(error is IOException || error is UnauthorizedAccessException ||
                            error is ObjectDisposedException || error is InvalidOperationException ||
                            error is ArgumentException || error is System.ComponentModel.Win32Exception)) throw;
                        if (!disposed) Thread.Sleep(100);
                    }
                }
            });
            thread.IsBackground = true;
            thread.Start();
        }

        internal static bool Forward(string name, LaunchOptions options)
        {
            try
            {
                using (var pipe = new NamedPipeClientStream(".", name, PipeDirection.Out))
                {
                    pipe.Connect(4000);
                    byte[] bytes = Encoding.UTF8.GetBytes(Wire.Json().Serialize(options.Handoff()) + "\n");
                    if (bytes.Length > 8192) return false;
                    pipe.Write(bytes, 0, bytes.Length);
                    return true;
                }
            }
            catch (Exception error)
            {
                if (!(error is IOException || error is TimeoutException ||
                    error is UnauthorizedAccessException)) throw;
                return false;
            }
        }

        public void Dispose() { disposed = true; }
    }
}
