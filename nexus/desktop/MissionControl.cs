using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Timer = System.Windows.Forms.Timer;

namespace Eos.Nexus
{
    internal sealed class MissionControl : Form
    {
        private readonly LaunchOptions options;
        private readonly Preferences preferences;
        private readonly SessionArchive archive;
        private readonly InstanceChannel instance;
        private readonly List<string> workspaces = new List<string>();
        private readonly Topology topology = new Topology { Dock = DockStyle.Fill };
        private readonly Radar radar = new Radar { Dock = DockStyle.Fill };
        private readonly ComboBox session = new ComboBox();
        private readonly TextBox search = new TextBox();
        private readonly ComboBox family = new ComboBox();
        private readonly ListBox timeline = new ListBox();
        private readonly ListBox skills = new ListBox();
        private readonly TreeView agents = new TreeView();
        private readonly RichTextBox inspector = new RichTextBox();
        private readonly RichTextBox runtime = new RichTextBox();
        private readonly List<Control> inspectorPages = new List<Control>();
        private readonly List<Button> inspectorButtons = new List<Button>();
        private readonly Label modeLabel = new Label();
        private readonly Label footer = new Label();
        private readonly Label securityText = new Label();
        private readonly TrackBar seek = new TrackBar();
        private readonly ComboBox speed = new ComboBox();
        private readonly Button pause = new Button();
        private readonly Button replayButton = new Button();
        private readonly Button play = new Button();
        private readonly NotifyIcon tray = new NotifyIcon();
        private readonly Timer pulse = new Timer { Interval = 250 };
        private readonly Stopwatch benchmark = Stopwatch.StartNew();
        private double sampleAt, firstCpu, firstObserverCpu;
        private long firstPaintedFrames, firstAnimatedFrames;
        private List<Observation> displayed = new List<Observation>();
        private List<Observation> replayEvents = new List<Observation>();
        private List<SessionInfo> knownArchives = new List<SessionInfo>();
        private List<GraphNode> graph = new List<GraphNode>();
        private Snapshot last = Snapshot.Empty;
        private Snapshot pending;
        private ObserverConnection connection;
        private int version, pendingDisconnect;
        private bool paused, replay, playing, flow, full, exiting, demo, updating, disconnected = true;
        private string selectedNode = "core", selection = "", notified = "";
        private DateTime lastArchiveScan = DateTime.MinValue, playbackTime;
        private long maxWorkingSet;
        private long maxCombinedWorkingSet;
        private bool tr { get { return preferences.Language == "tr"; } }

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

        internal MissionControl(LaunchOptions options, Preferences preferences, InstanceChannel instance)
        {
            this.options = options;
            this.preferences = preferences;
            this.instance = instance;
            archive = new SessionArchive(options.Home);
            demo = options.Demo;
            AddWorkspace(options.Workspace);
            Text = "EOS NEXUS";
            BackColor = Palette.Obsidian;
            ForeColor = Palette.Text;
            Font = Palette.Body;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? SystemIcons.Application;
            Size = new Size(1420, 910);
            MinimumSize = new Size(1030, 695);
            StartPosition = FormStartPosition.CenterScreen;
            KeyPreview = true;
            Build();

            Shown += (sender, args) =>
            {
                var enabled = 1;
                DwmSetWindowAttribute(Handle, 20, ref enabled, sizeof(int));
                instance.Attach(ReceiveHandoff);
                if ((options.Automatic || options.AtStartup) && preferences.OpenMinimized && options.SmokeSeconds == 0)
                    ToTray();
                else { StartConnection(); pulse.Start(); }
                RefreshSessions();
                RenderState();
            };
            Resize += (sender, args) =>
            {
                if (WindowState == FormWindowState.Minimized)
                {
                    StopConnection();
                    if (options.SmokeSeconds == 0) pulse.Stop();
                }
                else if (Visible && IsHandleCreated && !exiting) { StartConnection(); pulse.Start(); }
                UpdateRendering();
            };
            FormClosing += WindowClosing;
            pulse.Tick += (sender, args) => Heartbeat();
        }

        private string L(string english, string turkish) { return tr ? turkish : english; }

        private void Build()
        {
            var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 4, ColumnCount = 1,
                BackColor = Palette.Obsidian, Margin = new Padding(0), Padding = new Padding(12, 5, 12, 0) };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 103));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 150));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 27));
            Controls.Add(root);
            root.Controls.Add(BuildHeader(), 0, 0);

            var body = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = new Padding(0) };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 73));
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 27));
            root.Controls.Add(body, 0, 1);
            body.Controls.Add(topology, 0, 0);
            body.Controls.Add(BuildInspectors(), 1, 0);
            topology.Selected += SelectNode;

            var strip = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1,
                Margin = new Padding(0) };
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 25));
            root.Controls.Add(strip, 0, 2);
            SetupList(skills, L("Skills and lifecycle", "Beceriler ve yaşam döngüsü"));
            skills.SelectedIndexChanged += (sender, args) => InspectSkill();
            strip.Controls.Add(Card(L("SKILL INTELLIGENCE", "BECERİ İSTİHBARATI"), Palette.Violet, skills), 0, 0);
            strip.Controls.Add(BuildTimeline(), 1, 0);
            strip.Controls.Add(BuildSecurity(), 2, 0);

            footer.Dock = DockStyle.Fill;
            footer.TextAlign = ContentAlignment.MiddleLeft;
            footer.Font = Palette.Small;
            footer.ForeColor = Palette.Muted;
            root.Controls.Add(footer, 0, 3);

            tray.Text = "EOS NEXUS";
            tray.Icon = Icon;
            tray.DoubleClick += (sender, args) => Reopen();
            var menu = new ContextMenuStrip();
            menu.Items.Add(L("Open NEXUS", "NEXUS'u aç"), null, (sender, args) => Reopen());
            menu.Items.Add(L("Pause / resume", "Duraklat / sürdür"), null, (sender, args) => TogglePause());
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(L("Exit NEXUS", "NEXUS'tan çık"), null, (sender, args) => Exit());
            tray.ContextMenuStrip = menu;
        }

        private Control BuildHeader()
        {
            var header = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1,
                Margin = new Padding(0), BackColor = Palette.Obsidian };
            header.RowStyles.Add(new RowStyle(SizeType.Absolute, 49));
            header.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            var line = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false,
                FlowDirection = FlowDirection.LeftToRight, Margin = new Padding(0), Padding = new Padding(1, 5, 0, 0) };
            var title = new Label { Text = "EOS NEXUS", AutoSize = true, ForeColor = Palette.Text,
                Font = new Font("Segoe UI Semibold", 22), Margin = new Padding(0, 0, 13, 0) };
            line.Controls.Add(title);
            modeLabel.AutoSize = true;
            modeLabel.Font = Palette.Mono;
            modeLabel.Margin = new Padding(0, 12, 12, 0);
            modeLabel.AccessibleName = L("Connection mode", "Bağlantı modu");
            line.Controls.Add(modeLabel);
            header.Controls.Add(line, 0, 0);

            var controls = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false,
                Margin = new Padding(0), Padding = new Padding(0, 4, 0, 0) };
            SetupCombo(session, 265, L("Project and session", "Proje ve oturum"));
            session.SelectedIndexChanged += (sender, args) =>
            {
                if (updating) return;
                selection = (session.SelectedItem as Choice ?? Choice.Overview).Key;
                EndReplay();
                selectedNode = "core";
                RenderState();
            };
            controls.Controls.Add(session);
            controls.Controls.Add(ButtonFor(L("Add project", "Proje ekle"), AddProject));
            controls.Controls.Add(ButtonFor(L("Fit", "Sığdır"), () => topology.Fit()));
            controls.Controls.Add(ButtonFor("−", () => topology.Zoom(.85f), L("Zoom out", "Uzaklaştır")));
            controls.Controls.Add(ButtonFor("+", () => topology.Zoom(1.18f), L("Zoom in", "Yakınlaştır")));
            controls.Controls.Add(ButtonFor(L("Workflow", "İş akışı"), () => { flow = !flow; topology.Fit(); UpdateRendering(); }));
            controls.Controls.Add(ButtonFor(L("Fullscreen", "Tam ekran"), FullScreen));
            SetupButton(pause, L("Pause", "Duraklat"), TogglePause, null);
            controls.Controls.Add(pause);
            controls.Controls.Add(ButtonFor(L("Tray", "Tepsi"), ToTray));
            controls.Controls.Add(ButtonFor(L("Settings", "Ayarlar"), ChangePreferences));
            controls.Controls.Add(ButtonFor(L("Exit", "Çıkış"), Exit));
            header.Controls.Add(controls, 0, 1);
            return header;
        }

        private Control BuildInspectors()
        {
            var container = new TableLayoutPanel
            {
                Dock = DockStyle.Fill, Margin = new Padding(7, 0, 0, 0), BackColor = Palette.Panel,
                RowCount = 2, ColumnCount = 1, Padding = new Padding(8, 7, 8, 6)
            };
            container.RowStyles.Add(new RowStyle(SizeType.Absolute, 35));
            container.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            var row = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = new Padding(0) };
            var pages = new Panel { Dock = DockStyle.Fill, Padding = new Padding(4) };
            container.Controls.Add(row, 0, 0);
            container.Controls.Add(pages, 0, 1);
            ConfigureText(inspector);
            ConfigureText(runtime);
            agents.Dock = DockStyle.Fill;
            agents.BackColor = Palette.Panel;
            agents.ForeColor = Palette.Text;
            agents.BorderStyle = BorderStyle.None;
            agents.AccessibleName = L("Discovered and observed agents", "Bulunan ve gözlemlenen ajanlar");
            agents.AfterSelect += (sender, args) => { if (args.Node.Tag is string) SelectNode((string)args.Node.Tag); };
            AddInspectorPage(row, pages, L("Inspector", "İncele"), inspector);
            AddInspectorPage(row, pages, L("Agents", "Ajanlar"), agents);
            AddInspectorPage(row, pages, L("Runtime", "Çalışma"), runtime);
            SelectInspector(0);
            return container;
        }

        private void AddInspectorPage(Panel buttons, Panel pages, string title, Control content)
        {
            int index = inspectorPages.Count;
            var button = ButtonFor(title, () => SelectInspector(index));
            inspectorPages.Add(content);
            inspectorButtons.Add(button);
            pages.Controls.Add(content);
            buttons.Controls.Add(button);
        }

        private void SelectInspector(int index)
        {
            for (int i = 0; i < inspectorPages.Count; i++)
            {
                inspectorPages[i].Visible = i == index;
                inspectorButtons[i].ForeColor = i == index ? Palette.Cyan : Palette.Muted;
                inspectorButtons[i].FlatAppearance.BorderColor = i == index ? Palette.Cyan : Palette.Edge;
            }
        }

        private Control BuildTimeline()
        {
            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1,
                Margin = new Padding(0), Padding = new Padding(3, 0, 3, 0) };
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 29));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 27));
            var filters = new FlowLayoutPanel { Dock = DockStyle.Fill, Margin = new Padding(0), WrapContents = false };
            SetupCombo(family, 96, L("Event family", "Olay türü"));
            family.Items.Add(new Choice("", L("All events", "Tümü")));
            family.SelectedIndex = 0;
            family.SelectedIndexChanged += (sender, args) => { if (!updating) RenderTimeline(); };
            filters.Controls.Add(family);
            search.Width = 154;
            search.BorderStyle = BorderStyle.FixedSingle;
            search.BackColor = Palette.Obsidian;
            search.ForeColor = Palette.Text;
            search.AccessibleName = L("Search events", "Olaylarda ara");
            search.TextChanged += (sender, args) => RenderTimeline();
            filters.Controls.Add(search);
            SetupButton(replayButton, L("Replay", "Yeniden oynat"), ToggleReplay, null);
            filters.Controls.Add(replayButton);
            filters.Controls.Add(ButtonFor(L("Export", "Dışa aktar"), Export));
            layout.Controls.Add(filters, 0, 0);
            SetupList(timeline, L("Timeline", "Zaman çizelgesi"));
            timeline.SelectedIndexChanged += (sender, args) =>
            {
                var selected = timeline.SelectedItem as EventRow;
                if (!updating && selected != null) InspectEvent(selected.Event);
            };
            layout.Controls.Add(timeline, 0, 1);
            var player = new FlowLayoutPanel { Dock = DockStyle.Fill, Margin = new Padding(0), WrapContents = false };
            SetupButton(play, L("Play", "Oynat"), PlayPause, null);
            play.Enabled = false;
            player.Controls.Add(play);
            seek.Minimum = 0;
            seek.Maximum = 1;
            seek.Width = 171;
            seek.AutoSize = false;
            seek.Height = 25;
            seek.TickStyle = TickStyle.None;
            seek.Enabled = false;
            seek.AccessibleName = L("Replay position", "Oynatma konumu");
            seek.ValueChanged += (sender, args) =>
            {
                if (!updating && replay && replayEvents.Count > 0)
                {
                    playbackTime = replayEvents[seek.Value - 1 < 0 ? 0 : seek.Value - 1].Timestamp;
                    RenderState();
                }
            };
            player.Controls.Add(seek);
            SetupCombo(speed, 54, L("Replay speed", "Oynatma hızı"));
            speed.Items.AddRange(new object[] { "0.5×", "1×", "2×", "4×" });
            speed.SelectedIndex = 1;
            speed.Enabled = false;
            player.Controls.Add(speed);
            layout.Controls.Add(player, 0, 2);
            return Card(L("LIVE OPERATION STREAM", "CANLI İŞLEM AKIŞI"), Palette.Cyan, layout);
        }

        private Control BuildSecurity()
        {
            var panel = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1 };
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 66));
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 34));
            securityText.Dock = DockStyle.Fill;
            securityText.Font = Palette.Body;
            securityText.ForeColor = Palette.Muted;
            securityText.TextAlign = ContentAlignment.MiddleLeft;
            securityText.Padding = new Padding(7, 0, 0, 0);
            panel.Controls.Add(securityText, 0, 0);
            panel.Controls.Add(radar, 1, 0);
            return Card(L("SECURITY RADAR", "GÜVENLİK RADARI"), Palette.Green, panel);
        }

        private static Control Card(string caption, Color accent, Control body)
        {
            var table = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2,
                BackColor = Palette.Panel, Padding = new Padding(8, 2, 8, 2), Margin = new Padding(0, 5, 5, 0) };
            table.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
            table.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            var title = new Label { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft,
                Text = caption, ForeColor = accent, Font = Palette.Label, Margin = new Padding(1, 0, 0, 0) };
            table.Controls.Add(title, 0, 0);
            table.Controls.Add(body, 0, 1);
            return table;
        }

        private static void ConfigureText(RichTextBox box)
        {
            box.ReadOnly = true;
            box.BackColor = Palette.Panel;
            box.ForeColor = Palette.Text;
            box.Font = Palette.Body;
            box.DetectUrls = false;
            box.Dock = DockStyle.Fill;
            box.BorderStyle = BorderStyle.None;
        }

        private static void SetupList(ListBox list, string accessible)
        {
            list.Dock = DockStyle.Fill;
            list.BackColor = Palette.Panel;
            list.ForeColor = Palette.Text;
            list.BorderStyle = BorderStyle.None;
            list.IntegralHeight = false;
            list.AccessibleName = accessible;
            list.DrawMode = DrawMode.OwnerDrawFixed;
            list.ItemHeight = 19;
            list.DrawItem += (sender, args) =>
            {
                if (args.Index < 0 || args.Index >= list.Items.Count) return;
                var value = list.Items[args.Index];
                var row = value as EventRow;
                var skill = value as SkillRow;
                var color = row != null ? Palette.Status(row.Event.Verdict.Length > 0 ? row.Event.Verdict : row.Event.Status)
                    : skill != null ? Palette.Status(skill.Status) : Palette.Muted;
                using (var fill = new SolidBrush((args.State & DrawItemState.Selected) != 0
                    ? Color.FromArgb(28, 53, 70) : Palette.Panel))
                    args.Graphics.FillRectangle(fill, args.Bounds);
                var bounds = new Rectangle(args.Bounds.Left + 4, args.Bounds.Top, args.Bounds.Width - 6, args.Bounds.Height);
                TextRenderer.DrawText(args.Graphics, value.ToString(), Palette.Mono, bounds, color,
                    TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
                args.DrawFocusRectangle();
            };
        }

        private static void SetupCombo(ComboBox box, int width, string accessible)
        {
            box.FormattingEnabled = true;
            box.DropDownStyle = ComboBoxStyle.DropDownList;
            box.FlatStyle = FlatStyle.Flat;
            box.DrawMode = DrawMode.OwnerDrawFixed;
            box.Width = width;
            box.Margin = new Padding(3, 3, 6, 0);
            box.AccessibleName = accessible;
            box.BackColor = Palette.Panel;
            box.ForeColor = Palette.Text;
            box.Font = Palette.Body;
            box.DrawItem += (sender, args) =>
            {
                if (args.Index < 0 || args.Index >= box.Items.Count) return;
                bool selected = (args.State & DrawItemState.Selected) != 0;
                using (var fill = new SolidBrush(selected ? Color.FromArgb(26, 52, 72) : Palette.Panel))
                    args.Graphics.FillRectangle(fill, args.Bounds);
                TextRenderer.DrawText(args.Graphics, box.Items[args.Index].ToString(), Palette.Body, args.Bounds,
                    Palette.Text, TextFormatFlags.EndEllipsis | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
            };
        }

        private static Button ButtonFor(string caption, Action click, string accessibility = null)
        {
            var button = new Button();
            SetupButton(button, caption, click, accessibility);
            return button;
        }

        private static void SetupButton(Button button, string caption, Action click, string accessibility)
        {
            button.AutoSize = true;
            button.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            button.MinimumSize = new Size(30, 24);
            button.Height = 24;
            button.Margin = new Padding(2, 0, 3, 0);
            button.Padding = new Padding(4, 0, 4, 0);
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderColor = Palette.Edge;
            button.FlatAppearance.MouseOverBackColor = Color.FromArgb(20, 42, 59);
            button.BackColor = Palette.Panel;
            button.ForeColor = Palette.Text;
            button.Font = Palette.Body;
            button.Text = caption;
            button.AccessibleName = accessibility ?? caption;
            button.Click += (sender, args) => click();
        }

        private bool CanObserve { get { return Visible && WindowState != FormWindowState.Minimized && !demo && !exiting; } }

        private void StartConnection()
        {
            if (!CanObserve || connection != null || !IsHandleCreated) return;
            int generation = Interlocked.Increment(ref version);
            connection = new ObserverConnection(options, workspaces, preferences.LowResource, snapshot =>
            {
                if (generation != Interlocked.CompareExchange(ref version, 0, 0)) return;
                if (snapshot == null) Interlocked.Exchange(ref pendingDisconnect, 1);
                else
                {
                    Interlocked.Exchange(ref pendingDisconnect, 0);
                    Interlocked.Exchange(ref pending, snapshot);
                }
            });
            connection.Start();
        }

        private void StopConnection()
        {
            Interlocked.Increment(ref version);
            if (connection != null) { connection.Dispose(); connection = null; }
            Interlocked.Exchange(ref pending, null);
            Interlocked.Exchange(ref pendingDisconnect, 0);
        }

        private void ReceiveHandoff(Dictionary<string, object> message)
        {
            if (!IsHandleCreated || IsDisposed || exiting) return;
            try
            {
                BeginInvoke(new Action(() =>
                {
                    var request = new LaunchOptions { Workspace = Wire.String(message, "workspace"),
                        Automatic = Wire.Bool(message, "auto"), Session = Wire.String(message, "session"),
                        Host = Wire.String(message, "host") };
                    if (request.Automatic && !preferences.ClaimActivation(request)) return;
                    bool added = AddWorkspace(request.Workspace);
                    if (request.Session.Length > 0)
                    {
                        options.Session = request.Session;
                        options.Host = request.Host;
                        options.Workspace = request.Workspace;
                        selection = "";
                    }
                    if (added && CanObserve) { StopConnection(); StartConnection(); }
                    if (!request.Automatic || !preferences.OpenMinimized) Reopen();
                }));
            }
            catch (InvalidOperationException) { }
        }

        private bool AddWorkspace(string path)
        {
            if (String.IsNullOrWhiteSpace(path) || workspaces.Count >= 16) return false;
            if (!Path.IsPathRooted(path) || path.StartsWith(@"\\", StringComparison.Ordinal)) return false;
            try { path = Path.GetFullPath(path); }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
            catch (PathTooLongException) { return false; }
            if (workspaces.Contains(path, StringComparer.OrdinalIgnoreCase) || !Directory.Exists(path)) return false;
            workspaces.Add(path);
            return true;
        }

        private void AddProject()
        {
            using (var picker = new FolderBrowserDialog { Description = L("Select an EOS workspace", "EOS çalışma alanı seçin"),
                ShowNewFolderButton = false })
            {
                if (picker.ShowDialog(this) == DialogResult.OK && AddWorkspace(picker.SelectedPath))
                {
                    demo = false;
                    StopConnection();
                    StartConnection();
                }
            }
        }

        private void Heartbeat()
        {
            var update = Interlocked.Exchange(ref pending, null);
            bool dropped = Interlocked.Exchange(ref pendingDisconnect, 0) == 1;
            if (demo && last.Mode != "demo") update = Snapshot.Demo();
            if (update != null)
            {
                last = update;
                disconnected = false;
                archive.Store(last, preferences.RetentionDays);
                archive.Flush(preferences.RetentionDays, false);
                RefreshSessions();
                RenderState();
                NotifySecurity();
            }
            else if (dropped && !demo) { disconnected = true; RenderState(); }
            else if (playing) AdvancePlayback();
            else if (!disconnected && topology.Visible && last.Mode == "live"
                && DateTime.UtcNow - last.Received > TimeSpan.FromSeconds(9))
            {
                disconnected = true;
                RenderState();
            }

            if (options.SmokeSeconds <= 0) return;
            using (var process = Process.GetCurrentProcess())
            {
                long workingSet = ProcessMemory.WorkingSet(process);
                maxWorkingSet = Math.Max(maxWorkingSet, workingSet);
                var observer = connection == null ? new KeyValuePair<double, long>() : connection.Metrics();
                maxCombinedWorkingSet = Math.Max(maxCombinedWorkingSet, workingSet + observer.Value);
                if (sampleAt == 0 && benchmark.Elapsed.TotalSeconds >= Math.Min(3, options.SmokeSeconds / 3.0))
                {
                    sampleAt = benchmark.Elapsed.TotalSeconds;
                    firstCpu = process.TotalProcessorTime.TotalSeconds;
                    firstObserverCpu = observer.Key;
                    firstPaintedFrames = topology.PaintedFrames;
                    firstAnimatedFrames = topology.AnimatedFrames;
                }
            }
            if (benchmark.Elapsed.TotalSeconds >= options.SmokeSeconds) FinishSmoke();
        }

        private void RefreshSessions()
        {
            if (DateTime.UtcNow - lastArchiveScan >= TimeSpan.FromSeconds(15))
            {
                lastArchiveScan = DateTime.UtcNow;
                knownArchives = demo ? new List<SessionInfo>() : archive.Sessions(preferences.RetentionDays).ToList();
            }
            var liveKeys = new HashSet<string>(last.Sessions.Select(x => x.Key));
            var choices = new List<Choice> { new Choice("", L("All projects · overview", "Tüm projeler · genel görünüm")) };
            choices.AddRange(last.Sessions.Concat(knownArchives.Where(x => !liveKeys.Contains(x.Key)))
                .OrderByDescending(x => x.LastSeen).Select(x =>
                {
                    string project;
                    if (!last.Projects.TryGetValue(x.Project, out project) || project.Length == 0) project = x.Project;
                    string host = x.Host.Length == 0 ? L("unknown", "bilinmiyor") : x.Host;
                    string shortSession = x.Id.Length > 14 ? x.Id.Substring(0, 14) + "…" : x.Id;
                    return new Choice(x.Key, project + " · " + host + " · " + shortSession, x);
                }));
            if (choices.FindIndex(x => x.Key == selection) < 0 || (selection.Length == 0 && options.Session != null))
            {
                string projected = options.ProjectedSession();
                var initial = choices.FirstOrDefault(x => x.Info != null && x.Info.Id == projected
                    && (options.Host == null || options.Host.Equals(x.Info.Host, StringComparison.OrdinalIgnoreCase)));
                selection = initial == null ? "" : initial.Key;
                if (initial != null) options.Session = null;
            }
            if (selection.Length == 0 && choices.Count == 2 && session.Items.Count <= 1) selection = choices[1].Key;
            updating = true;
            session.BeginUpdate();
            session.Items.Clear();
            session.Items.AddRange(choices.Cast<object>().ToArray());
            session.SelectedIndex = Math.Max(0, choices.FindIndex(x => x.Key == selection));
            session.EndUpdate();
            updating = false;
        }

        private SessionInfo SelectedSession { get { return (session.SelectedItem as Choice ?? Choice.Overview).Info; } }

        private string CurrentMode()
        {
            if (replay) return "REPLAY";
            if (demo) return "DEMO";
            if (!CanObserve || connection == null) return "DISCONNECTED";
            if (disconnected || last == Snapshot.Empty) return "DISCONNECTED";
            if (DateTime.UtcNow - last.Received > TimeSpan.FromSeconds(9)) return "DISCONNECTED";
            if (last.Mode == "disconnected") return "DISCONNECTED";
            var info = SelectedSession;
            string mode = info == null ? last.Mode : info.Mode;
            return mode == "live" ? "LIVE" : mode == "stale" ? "STALE" : "DISCONNECTED";
        }

        private void RenderState()
        {
            string mode = CurrentMode();
            modeLabel.Text = "●  " + ModeName(mode) + (paused ? L("  ·  PAUSED", "  ·  DURAKLATILDI") : "");
            modeLabel.ForeColor = mode == "LIVE" ? Palette.Cyan : mode == "DEMO" || mode == "REPLAY"
                ? Palette.Violet : mode == "STALE" ? Palette.Amber : Palette.Muted;
            var info = SelectedSession;
            if (replay) displayed = replayEvents.Take(seek.Value).ToList();
            else if (info == null) displayed = new List<Observation>();
            else displayed = last.Events.Where(info.Contains).ToList();
            graph = BuildGraph(displayed, mode);
            PopulateSkills();
            PopulateAgents();
            RenderRuntime();
            RenderTimeline();
            RenderSelection(false);
            var security = displayed.Where(x => x.IsSecurity).ToList();
            radar.Update(security);
            string denial = L("Denials", "Ret") + "  " + security.Count(x => x.Verdict == "DENY" || x.Status == "DENIED");
            securityText.Text = security.Count == 0 ? L("No security evidence in view", "Görünümde güvenlik kanıtı yok")
                : L("Observed decisions", "Gözlemlenen kararlar") + "  " + security.Count + "\n" + denial;
            string endpoint = info == null ? L("Select a session to inspect events", "Olayları incelemek için oturum seçin")
                : L("Read-only local observation", "Salt okunur yerel gözlem");
            footer.Text = endpoint + "  ·  " + last.Sessions.Count.ToString(CultureInfo.InvariantCulture)
                + L(" sessions", " oturum") + (last.Issues.Count > 0
                    ? "  ·  " + L("Source issues", "Kaynak sorunları") + " " + String.Join(", ", last.Issues
                        .Select(x => x + " " + DisplayValue(Wire.Get(last.IssueCounts, x)))) : "");
            UpdateRendering();
        }

        private string ModeName(string mode)
        {
            if (!tr) return mode;
            switch (mode)
            {
                case "LIVE": return "CANLI";
                case "STALE": return "GÜNCEL DEĞİL";
                case "REPLAY": return "TEKRAR";
                case "DEMO": return "DEMO · SİMÜLASYON";
                default: return "BAĞLANTI YOK";
            }
        }

        private void UpdateRendering()
        {
            if (!Visible || WindowState == FormWindowState.Minimized) { topology.SuspendMotion(); return; }
            string mode = CurrentMode();
            bool animate = !paused && preferences.Animations && !preferences.ReducedMotion
                && (mode == "LIVE" || mode == "DEMO" || (mode == "REPLAY" && playing));
            topology.Render(graph, animate, preferences.LowResource, flow,
                L("Drag to pan  ·  Wheel to zoom  ·  F11 fullscreen", "Sürükle: kaydır  ·  Tekerlek: yakınlaştır  ·  F11: tam ekran"));
        }

        private List<GraphNode> BuildGraph(List<Observation> items, string mode)
        {
            var result = new List<GraphNode>();
            var agentIds = last.Items("AGENT_REGISTRY").Concat(items.Select(x => x.Agent))
                .Where(x => x.Length > 0).Distinct(StringComparer.Ordinal).OrderBy(x => x).ToList();
            var core = items.Where(x => x.Agent == "chief-architect").ToList();
            var coreNode = Node("core", "CEO", Palette.Cyan, core,
                agentIds.Contains("chief-architect") ? "DISCOVERED" : "UNKNOWN", mode);
            result.Add(coreNode);
            result.Add(Node("router", L("EOS ROUTER", "EOS YÖNLENDİRİCİ"), Palette.Cyan,
                items.Where(x => Matches(x, "router", "route", "query", "activation")).ToList(), "UNKNOWN", mode));
            result.Add(Node("skill", L("SKILL INTELLIGENCE", "BECERİ İSTİHBARATI"), Palette.Violet,
                items.Where(x => x.Skill.Length > 0).ToList(),
                last.Items("SKILL_INDEX").Any() ? "DISCOVERED" : "UNKNOWN", mode));
            result.Add(Node("tools", L("MCP / TOOLS", "MCP / ARAÇLAR"), Palette.Violet,
                items.Where(x => x.Tool.Length > 0 || Matches(x, "mcp", "query")).ToList(), "UNKNOWN", mode));
            result.Add(Node("model", L("MODEL / PROVIDER", "MODEL / SAĞLAYICI"), Palette.Violet,
                items.Where(x => x.Read("model").Length > 0 || x.Read("provider").Length > 0).ToList(), "UNKNOWN", mode));
            result.Add(Node("security", L("GUARDIAN / EDR", "GUARDIAN / EDR"), Palette.Green,
                items.Where(x => x.IsSecurity).ToList(), "UNKNOWN", mode));
            result.Add(Node("gate", L("QUALITY GATE / EVIDENCE", "KALİTE KAPISI / KANIT"), Palette.Green,
                items.Where(x => Matches(x, "gate", "quality_gate", "evidence")).ToList(), "UNKNOWN", mode));
            result.Add(Node("projects", L("PROJECT CONTEXT", "PROJE BAĞLAMI"), Palette.Violet,
                items.Where(x => Matches(x, "context", "project", "activation")).ToList(),
                last.Projects.Count > 0 ? "DISCOVERED" : "UNKNOWN", mode));
            var graphEvidence = items.Where(x => Matches(x, "graph", "graphify")).ToList();
            string graphProject = SelectedSession == null && last.Projects.Count == 1
                ? last.Projects.Keys.First() : SelectedSession == null ? "" : SelectedSession.Project;
            Observation graphBuild;
            if (!replay && last.Graphify.TryGetValue(graphProject, out graphBuild)) graphEvidence.Add(graphBuild);
            result.Add(Node("graphify", "GRAPHIFY", Palette.Violet, graphEvidence.OrderBy(x => x.Timestamp).ToList(), "UNKNOWN", mode));
            var orchestrators = last.Items("ORCHESTRATORS").ToList();
            var adapters = new HashSet<string>(last.AdapterOnly("ORCHESTRATORS"), StringComparer.Ordinal);
            string orchestratorState = orchestrators.Count == 0 ? "UNKNOWN"
                : orchestrators.All(adapters.Contains) ? "ADAPTER_ONLY" : "DISCOVERED";
            result.Add(Node("orchestrators", L("ORCHESTRATORS", "ORKESTRATÖRLER"), Palette.Violet,
                items.Where(x => Matches(x, "orchestrator", "orchestration", "delegation")).ToList(),
                orchestratorState, mode));
            result.Add(Node("recovery", L("RECOVERY / PROMOTION", "KURTARMA / TERFİ"), Palette.Violet,
                items.Where(x => Matches(x, "recovery", "promotion", "rollback", "ledger")).ToList(), "UNKNOWN", mode));

            foreach (string agent in agentIds.Where(x => x != "chief-architect")
                .OrderByDescending(x => items.Any(e => e.Agent == x && e.Status == "ACTIVE"))
                .ThenByDescending(x => items.Any(e => e.Agent == x)).Take(10))
            {
                var evidence = items.Where(x => x.Agent == agent).ToList();
                var node = Node("agent:" + agent, agent.Replace('-', ' ').ToUpperInvariant(), Palette.Cyan, evidence,
                    last.Items("AGENT_REGISTRY").Contains(agent) ? "DISCOVERED" : "UNKNOWN", mode);
                string parent = evidence.Select(x => x.Parent).LastOrDefault(x => x.Length > 0 && x != agent);
                if (parent != null && parent != "chief-architect") node.Parent = "agent:" + parent;
                result.Add(node);
            }
            if (result.Any(x => x.Active && x.Key.StartsWith("agent:", StringComparison.Ordinal))) coreNode.Active = true;
            foreach (var node in result)
                if (node.Parent != null && !result.Any(x => x.Key == node.Parent)) node.Parent = null;
            return result;
        }

        private static bool Matches(Observation item, params string[] families)
        {
            return families.Any(x => item.Family.Equals(x, StringComparison.OrdinalIgnoreCase)
                || item.Kind.Equals(x, StringComparison.OrdinalIgnoreCase)
                || item.Kind.StartsWith(x + ".", StringComparison.OrdinalIgnoreCase));
        }

        private static GraphNode Node(string key, string title, Color accent, List<Observation> items,
            string initial, string mode)
        {
            string status = initial;
            bool active = false;
            if (items.Count > 0)
            {
                var latest = items.LastOrDefault(x => x.Status != "UNKNOWN") ?? items.Last();
                status = latest.Verdict.Length != 0 && latest.Verdict != "UNKNOWN" ? latest.Verdict : latest.Status;
                if (status.Length == 0) status = initial;
                active = (mode == "LIVE" || mode == "DEMO" || mode == "REPLAY")
                    && items.GroupBy(x => x.Correlation.Length > 0 ? x.Correlation : x.Subject)
                        .Any(x => x.Last().Status == "ACTIVE" || x.Last().Status == "IN_USE");
                if (!active && (status == "ACTIVE" || status == "IN_USE")) status = "STALE";
            }
            return new GraphNode { Key = key, Name = title, Accent = accent, Status = status, Active = active, Evidence = items };
        }

        private void PopulateSkills()
        {
            var rows = displayed.Where(x => x.Skill.Length != 0).GroupBy(x => x.Skill)
                .ToDictionary(x => x.Key, x => x.Last(), StringComparer.Ordinal);
            var discovered = last.Items("SKILL_INDEX").Concat(rows.Keys).Distinct(StringComparer.Ordinal)
                .OrderBy(x => x).ToArray();
            var prior = skills.SelectedItem as SkillRow;
            updating = true;
            skills.BeginUpdate();
            skills.Items.Clear();
            foreach (string name in discovered)
            {
                Observation observation;
                rows.TryGetValue(name, out observation);
                string status = observation == null ? "DISCOVERED" : observation.Status;
                if (!new[] { "DISCOVERED", "SELECTED", "LOADED", "IN_USE", "COMPLETED", "REJECTED",
                    "UNAVAILABLE", "UNKNOWN" }.Contains(status)) status = "UNKNOWN";
                var item = new SkillRow(name, status, observation);
                skills.Items.Add(item);
                if (prior != null && prior.Name == item.Name) skills.SelectedItem = item;
            }
            skills.EndUpdate();
            updating = false;
        }

        private void PopulateAgents()
        {
            var expanded = new HashSet<string>();
            CollectExpanded(agents.Nodes, expanded);
            var nodes = new Dictionary<string, TreeNode>();
            var evidence = displayed.Where(x => x.Agent.Length > 0).GroupBy(x => x.Agent)
                .ToDictionary(x => x.Key, x => x.ToList(), StringComparer.Ordinal);
            var ids = last.Items("AGENT_REGISTRY").Concat(evidence.Keys)
                .Distinct(StringComparer.Ordinal).OrderBy(x => x).ToList();
            foreach (string id in ids)
            {
                List<Observation> items;
                evidence.TryGetValue(id, out items);
                string tag = id == "chief-architect" ? "core" : "agent:" + id;
                var model = Node(tag, id, Palette.Cyan, items ?? new List<Observation>(),
                    "DISCOVERED", CurrentMode());
                nodes[id] = new TreeNode((id == "chief-architect" ? "CEO  /  " : "") + id + "  ·  " + model.Status)
                    { Name = id, Tag = tag, ForeColor = Palette.Status(model.Status) };
            }
            agents.BeginUpdate();
            agents.Nodes.Clear();
            var parents = evidence.ToDictionary(x => x.Key,
                x => x.Value.Select(y => y.Parent).LastOrDefault(y => y.Length > 0));
            foreach (string id in ids)
            {
                string parent;
                parents.TryGetValue(id, out parent);
                if (parent != null && nodes.ContainsKey(parent) && !HasCycle(id, parents))
                    nodes[parent].Nodes.Add(nodes[id]);
                else agents.Nodes.Add(nodes[id]);
            }
            foreach (string id in ids.Where(expanded.Contains)) nodes[id].Expand();
            agents.EndUpdate();
        }

        private static void CollectExpanded(TreeNodeCollection nodes, HashSet<string> expanded)
        {
            foreach (TreeNode node in nodes)
            {
                if (node.IsExpanded) expanded.Add(node.Name);
                if (node.Nodes.Count > 0) CollectExpanded(node.Nodes, expanded);
            }
        }

        private static bool HasCycle(string child, Dictionary<string, string> parents)
        {
            var seen = new HashSet<string>();
            string current = child;
            while (current != null && parents.ContainsKey(current))
            {
                if (!seen.Add(current)) return true;
                current = parents[current];
            }
            return false;
        }

        private void RenderRuntime()
        {
            var info = SelectedSession;
            var sb = new StringBuilder();
            sb.AppendLine(L("RUNTIME INTELLIGENCE", "ÇALIŞMA ZAMANI İSTİHBARATI"));
            sb.AppendLine(L("Host", "Host") + "  " + Unknown(info == null ? "" : info.Host));
            sb.AppendLine(L("Model", "Model") + "  " + Unknown(LastField("model")));
            sb.AppendLine(L("Provider", "Sağlayıcı") + "  " + Unknown(LastField("provider")));
            foreach (string key in new[] { "input_tokens", "output_tokens", "cost", "duration_seconds" })
            {
                var item = displayed.LastOrDefault(x => Wire.Get(x.Fields, key) != null);
                sb.AppendLine(FieldName(key) + "  " + (item == null ? Unknown("") : DisplayValue(Wire.Get(item.Fields, key))));
            }

            sb.AppendLine();
            sb.AppendLine(L("PROJECT CONTEXT", "PROJE BAĞLAMI"));
            if (info == null)
            {
                foreach (var project in last.Projects.Take(48))
                    sb.AppendLine(project.Value.Length > 0 ? project.Value : project.Key);
                if (last.Projects.Count == 0) sb.AppendLine(Unknown(""));
            }
            else
            {
                string label;
                sb.AppendLine(last.Projects.TryGetValue(info.Project, out label) ? label : info.Project);
                sb.AppendLine(L("Session", "Oturum") + "  " + info.Id);
                if (info.LastSeen != DateTime.MinValue)
                    sb.AppendLine(L("Last event", "Son olay") + "  " + info.LastSeen.ToLocalTime().ToString("G"));
            }

            sb.AppendLine();
            sb.AppendLine(L("DISCOVERED CAPABILITIES", "BULUNAN YETENEKLER"));
            foreach (var entry in last.Registries.Take(32))
            {
                string state = Wire.String(Wire.Map(entry.Value), "state");
                sb.AppendLine(entry.Key + "  /  " + Unknown(state));
                var adapters = new HashSet<string>(last.AdapterOnly(entry.Key), StringComparer.Ordinal);
                foreach (string id in last.Items(entry.Key).Take(12))
                    sb.AppendLine("  " + id + (adapters.Contains(id) ? "  ·  ADAPTER_ONLY" : ""));
            }
            if (last.Registries.Count == 0) sb.AppendLine(Unknown(""));
            SetText(runtime, sb.ToString());
        }

        private void RenderTimeline()
        {
            string selectedFamily = (family.SelectedItem as Choice ?? Choice.Overview).Key;
            string[] names = displayed.Select(x => x.Family).Where(x => x.Length > 0)
                .Distinct(StringComparer.Ordinal).OrderBy(x => x).ToArray();
            if (family.Items.Count != names.Length + 1 ||
                !names.SequenceEqual(family.Items.Cast<Choice>().Skip(1).Select(x => x.Key)))
            {
                updating = true;
                family.BeginUpdate();
                family.Items.Clear();
                family.Items.Add(new Choice("", L("All events", "Tümü")));
                foreach (string name in names) family.Items.Add(new Choice(name, name));
                family.SelectedIndex = Math.Max(0, Array.IndexOf(names, selectedFamily) + 1);
                family.EndUpdate();
                updating = false;
            }

            var prior = timeline.SelectedItem as EventRow;
            updating = true;
            timeline.BeginUpdate();
            timeline.Items.Clear();
            string query = search.Text.Trim();
            foreach (var item in displayed.AsEnumerable().Reverse().Where(x =>
                (selectedFamily.Length == 0 || x.Family == selectedFamily) && (query.Length == 0 ||
                (x.Subject + " " + x.Status + " " + x.Family + " " + x.Kind + " " + x.Agent + " " + x.Tool)
                .IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0)))
            {
                var row = new EventRow(item);
                timeline.Items.Add(row);
                if (prior != null && prior.Event.Id == item.Id) timeline.SelectedItem = row;
            }
            if (timeline.Items.Count == 0)
                timeline.Items.Add(SelectedSession == null ? L("Choose a project session above", "Yukarıdan bir proje oturumu seçin")
                    : L("No matching observations", "Eşleşen gözlem yok"));
            timeline.EndUpdate();
            updating = false;
        }

        private void SelectNode(string key)
        {
            selectedNode = key;
            topology.FocusNode(key);
            RenderSelection(true);
        }

        private void RenderSelection(bool activateTab)
        {
            GraphNode model = graph.FirstOrDefault(x => x.Key == selectedNode);
            if (model == null && selectedNode.StartsWith("agent:", StringComparison.Ordinal))
            {
                string id = selectedNode.Substring(6);
                var events = displayed.Where(x => x.Agent == id).ToList();
                bool found = last.Items("AGENT_REGISTRY").Contains(id);
                model = Node(selectedNode, id, Palette.Cyan, events, found ? "DISCOVERED" : "UNKNOWN", CurrentMode());
            }
            if (model == null) return;

            var sb = new StringBuilder();
            sb.AppendLine(model.Name);
            sb.AppendLine(L("State", "Durum") + "  " + model.Status);
            if (model.Parent != null) sb.AppendLine(L("Parent", "Üst öğe") + "  " + model.Parent);
            if (model.Key == "skill" || model.Key == "orchestrators")
            {
                var ids = last.Items(model.Key == "skill" ? "SKILL_INDEX" : "ORCHESTRATORS").Take(30).ToArray();
                if (ids.Length > 0) sb.AppendLine(L("Discovered", "Bulunan") + "  " + String.Join(", ", ids));
            }
            if (model.Evidence.Count > 0)
            {
                sb.AppendLine();
                sb.AppendLine(L("LATEST OBSERVATION", "SON GÖZLEM"));
                AppendEvent(sb, model.Evidence.Last());
            }
            else
            {
                sb.AppendLine();
                sb.AppendLine(L("No session evidence for this node", "Bu düğüm için oturum kanıtı yok"));
            }
            SetText(inspector, sb.ToString());
            if (activateTab) SelectInspector(0);
        }

        private void InspectSkill()
        {
            var row = skills.SelectedItem as SkillRow;
            if (updating || row == null) return;
            var sb = new StringBuilder(row.Name).AppendLine().AppendLine(L("State", "Durum") + "  " + row.Status);
            if (row.Event == null) sb.AppendLine(L("Source  EOS skill registry", "Kaynak  EOS beceri kaydı"));
            else AppendEvent(sb.AppendLine(), row.Event);
            SetText(inspector, sb.ToString());
            SelectInspector(0);
        }

        private void InspectEvent(Observation entry)
        {
            var sb = new StringBuilder(entry.Subject).AppendLine().AppendLine();
            AppendEvent(sb, entry);
            SetText(inspector, sb.ToString());
            SelectInspector(0);
        }

        private void AppendEvent(StringBuilder sb, Observation item)
        {
            sb.AppendLine(L("Time", "Zaman") + "  " + item.Timestamp.ToLocalTime().ToString("G"));
            foreach (string key in new[] { "kind", "status", "verdict", "severity", "project", "task", "session", "host",
                "agent", "parent", "skill", "tool", "server", "query", "source", "evidence", "correlation", "provider",
                "model", "version", "input_tokens", "output_tokens", "cost", "currency", "duration_seconds", "input_bytes",
                "output_bytes", "argument_count", "mechanism", "artifact_sha256" })
            {
                object data = Wire.Get(item.Fields, key);
                if (data != null && DisplayValue(data).Length > 0)
                    sb.AppendLine(FieldName(key) + "  " + DisplayValue(data));
            }
        }

        private string FieldName(string key)
        {
            if (!tr) return CultureInfo.InvariantCulture.TextInfo.ToTitleCase(key.Replace('_', ' '));
            switch (key)
            {
                case "kind": return "Olay";
                case "status": return "Durum";
                case "verdict": return "Karar";
                case "severity": return "Önem";
                case "project": return "Proje";
                case "task": return "Görev";
                case "session": return "Oturum";
                case "agent": return "Ajan";
                case "parent": return "Üst öğe";
                case "skill": return "Beceri";
                case "tool": return "Araç";
                case "source": return "Kaynak";
                case "evidence": return "Kanıt";
                case "correlation": return "Korelasyon";
                case "provider": return "Sağlayıcı";
                case "reason": return "Gerekçe";
                case "input_tokens": return "Girdi token";
                case "output_tokens": return "Çıktı token";
                case "cost": return "Maliyet";
                case "duration_seconds": return "Süre (sn)";
                case "tool_input": return "Araç girdisi";
                case "tool_result": return "Araç çıktısı";
                default: return key;
            }
        }

        private static string DisplayValue(object data)
        {
            if (!(data is string || data is int || data is long || data is double || data is decimal || data is bool))
                return "";
            string text = Convert.ToString(data, CultureInfo.InvariantCulture);
            return text.Length > 1024 ? text.Substring(0, 1024) + "…" : text;
        }

        private string LastField(string key)
        {
            var item = displayed.LastOrDefault(x => x.Read(key).Length > 0);
            return item == null ? "" : item.Read(key);
        }

        private string Unknown(string value) { return value.Length == 0 ? L("Unknown", "Bilinmiyor") : value; }
        private static void SetText(RichTextBox box, string value) { if (box.Text != value) box.Text = value; }

        private void ToggleReplay()
        {
            if (replay) { EndReplay(); RenderState(); return; }
            var info = SelectedSession;
            if (info == null) return;
            replayEvents = archive.Load(info, preferences.RetentionDays).Concat(last.Events.Where(info.Contains))
                .GroupBy(x => x.Id).Select(x => x.Last()).OrderBy(x => x.Timestamp).Take(500).ToList();
            if (replayEvents.Count == 0) return;
            replay = true;
            playing = false;
            updating = true;
            seek.Enabled = play.Enabled = speed.Enabled = true;
            seek.Maximum = replayEvents.Count;
            seek.Value = 1;
            playbackTime = replayEvents[0].Timestamp;
            replayButton.Text = L("End replay", "Tekrarı bitir");
            updating = false;
            RenderState();
        }

        private void EndReplay()
        {
            replay = playing = false;
            seek.Enabled = play.Enabled = speed.Enabled = false;
            seek.Value = 0;
            play.Text = L("Play", "Oynat");
            replayButton.Text = L("Replay", "Yeniden oynat");
        }

        private void PlayPause()
        {
            if (!replay || replayEvents.Count == 0) return;
            if (!playing && seek.Value >= replayEvents.Count) seek.Value = 1;
            playing = !playing;
            play.Text = playing ? L("Pause", "Duraklat") : L("Play", "Oynat");
            UpdateRendering();
        }

        private void AdvancePlayback()
        {
            double multiplier = new[] { .5, 1, 2, 4 }[Math.Max(0, speed.SelectedIndex)];
            playbackTime = playbackTime.AddMilliseconds(pulse.Interval * multiplier);
            int index = replayEvents.FindLastIndex(x => x.Timestamp <= playbackTime) + 1;
            if (seek.Value != index)
            {
                updating = true;
                seek.Value = index;
                updating = false;
                RenderState();
            }
            if (index >= replayEvents.Count) PlayPause();
        }

        private void Export()
        {
            if (SelectedSession == null || displayed.Count == 0) return;
            using (var save = new SaveFileDialog { FileName = "eos-nexus-session.json", DefaultExt = "json",
                Filter = "JSON (*.json)|*.json", Title = L("Export current session", "Oturumu dışa aktar") })
            {
                if (save.ShowDialog(this) != DialogResult.OK) return;
                try
                {
                    Wire.WriteAtomic(save.FileName, Wire.Json().Serialize(new Dictionary<string, object>
                    {
                        { "schema", 1 }, { "mode", demo ? "demo" : "recorded" },
                        { "events", displayed.Select(x => x.Fields).ToArray() }
                    }));
                }
                catch (Exception error)
                {
                    if (!(error is UnauthorizedAccessException || error is IOException ||
                        error is InvalidOperationException)) throw;
                    DisplayError(L("The session could not be exported.", "Oturum dışa aktarılamadı."));
                }
            }
        }

        private void TogglePause()
        {
            paused = !paused;
            pause.Text = paused ? L("Resume", "Sürdür") : L("Pause", "Duraklat");
            RenderState();
        }

        private void FullScreen()
        {
            full = !full;
            FormBorderStyle = full ? FormBorderStyle.None : FormBorderStyle.Sizable;
            WindowState = full ? FormWindowState.Maximized : FormWindowState.Normal;
        }

        protected override bool ProcessCmdKey(ref Message message, Keys key)
        {
            if (key == Keys.F11 || (full && key == Keys.Escape)) { FullScreen(); return true; }
            if (key == (Keys.Control | Keys.F)) { search.Focus(); return true; }
            if (key == (Keys.Control | Keys.D0)) { topology.Fit(); return true; }
            return base.ProcessCmdKey(ref message, key);
        }

        private void ToTray()
        {
            Hide();
            if (playing) PlayPause();
            pulse.Stop();
            topology.SuspendMotion();
            StopConnection();
            tray.Visible = true;
        }

        private void Reopen()
        {
            Show();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            tray.Visible = false;
            Activate();
            StartConnection();
            pulse.Start();
            RenderState();
        }

        private void Exit()
        {
            exiting = true;
            Close();
        }

        private void WindowClosing(object sender, FormClosingEventArgs args)
        {
            if (!exiting && args.CloseReason == CloseReason.UserClosing && preferences.CloseToTray &&
                options.SmokeSeconds == 0)
            {
                args.Cancel = true;
                ToTray();
                return;
            }
            exiting = true;
            pulse.Stop();
            topology.SuspendMotion();
            StopConnection();
            archive.Flush(preferences.RetentionDays, true);
            tray.Visible = false;
        }

        private void NotifySecurity()
        {
            if (!preferences.Notifications || preferences.Quiet || CurrentMode() != "LIVE") return;
            var denial = displayed.LastOrDefault(x => x.IsSecurity && (x.Verdict == "DENY" || x.Status == "DENIED"));
            if (denial == null || denial.Id == notified || DateTime.UtcNow - denial.Timestamp > TimeSpan.FromSeconds(7)) return;
            notified = denial.Id;
            // Keep sensitive event content out of shell notification history.
            tray.Visible = true;
            tray.ShowBalloonTip(3500, "EOS NEXUS", L("EOS reported a security denial in this session.",
                "EOS bu oturumda bir güvenlik reddi bildirdi."), ToolTipIcon.Warning);
        }

        private void DisplayError(string message)
        {
            MessageBox.Show(this, message, "EOS NEXUS", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        private void ChangePreferences()
        {
            using (var form = new Form
            {
                Text = L("NEXUS settings", "NEXUS ayarları"), StartPosition = FormStartPosition.CenterParent,
                ClientSize = new Size(420, 440), FormBorderStyle = FormBorderStyle.FixedDialog, BackColor = Palette.Obsidian,
                ForeColor = Palette.Text, Font = Palette.Body, MinimizeBox = false, MaximizeBox = false, ShowInTaskbar = false
            })
            {
                var items = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
                    WrapContents = false, Padding = new Padding(14) };
                form.Controls.Add(items);
                var auto = Option(items, L("Open on EOS activation", "EOS etkinleştiğinde aç"), preferences.AutoOpen);
                var mini = Option(items, L("Open automatically in tray", "Otomatik açılışı tepside başlat"), preferences.OpenMinimized);
                var close = Option(items, L("Closing the window keeps NEXUS in tray", "Pencereyi kapatınca NEXUS'u tepside tut"), preferences.CloseToTray);
                var animation = Option(items, L("Enable animations", "Animasyonları etkinleştir"), preferences.Animations);
                var reduced = Option(items, L("Reduced motion", "Azaltılmış hareket"), preferences.ReducedMotion);
                var low = Option(items, L("Low-resource mode", "Düşük kaynak modu"), preferences.LowResource);
                var quiet = Option(items, L("Quiet mode", "Sessiz mod"), preferences.Quiet);
                var notes = Option(items, L("Security denial notifications", "Güvenlik reddi bildirimleri"), preferences.Notifications);
                var startup = Option(items, L("Start NEXUS with Windows", "NEXUS'u Windows ile başlat"), preferences.StartWithWindows);
                var lang = new ComboBox();
                SetupCombo(lang, 140, L("Interface language", "Arayüz dili"));
                lang.Items.AddRange(new object[] { "English", "Türkçe" });
                lang.SelectedIndex = tr ? 1 : 0;
                items.Controls.Add(lang);
                var prompt = new Label { AutoSize = true, Text = L("Replay archive retention in days (0 disables storage)",
                    "Oturum arşivi saklama süresi, gün (0: kayıt kapalı)") };
                items.Controls.Add(prompt);
                var retention = new NumericUpDown { Minimum = 0, Maximum = 30, Value = preferences.RetentionDays, Width = 80,
                    AccessibleName = prompt.Text };
                items.Controls.Add(retention);
                var actions = new FlowLayoutPanel { AutoSize = true };
                var save = ButtonFor(L("Save", "Kaydet"), () => form.DialogResult = DialogResult.OK);
                actions.Controls.Add(save);
                actions.Controls.Add(ButtonFor(L("Cancel", "İptal"), () => form.DialogResult = DialogResult.Cancel));
                items.Controls.Add(actions);
                form.AcceptButton = save;
                if (form.ShowDialog(this) != DialogResult.OK) return;
                bool languageChanged = (lang.SelectedIndex == 1) != tr;
                bool restartObserver = low.Checked != preferences.LowResource;
                try
                {
                    if (startup.Checked != preferences.StartWithWindows)
                        Preferences.ApplyStartup(startup.Checked, options.Home);
                    preferences.AutoOpen = auto.Checked;
                    preferences.OpenMinimized = mini.Checked;
                    preferences.CloseToTray = close.Checked;
                    preferences.Animations = animation.Checked;
                    preferences.ReducedMotion = reduced.Checked;
                    preferences.LowResource = low.Checked;
                    preferences.Quiet = quiet.Checked;
                    preferences.Notifications = notes.Checked;
                    preferences.StartWithWindows = startup.Checked;
                    preferences.Language = lang.SelectedIndex == 1 ? "tr" : "en";
                    preferences.RetentionDays = (int)retention.Value;
                    preferences.Save();
                    archive.Flush(preferences.RetentionDays, true);
                    lastArchiveScan = DateTime.MinValue;
                    if (restartObserver) { StopConnection(); StartConnection(); }
                    RefreshSessions();
                    RenderState();
                    if (languageChanged) MessageBox.Show(this, L("Reopen NEXUS to apply the interface language everywhere.",
                        "Arayüz dilini tüm ekrana uygulamak için NEXUS'u yeniden açın."), "EOS NEXUS",
                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                catch (Exception error)
                {
                    if (!(error is IOException || error is UnauthorizedAccessException || error is InvalidOperationException
                        || error is System.Security.SecurityException)) throw;
                    DisplayError(L("The settings could not be saved.", "Ayarlar kaydedilemedi."));
                }
            }
        }

        private static CheckBox Option(FlowLayoutPanel panel, string name, bool value)
        {
            var box = new CheckBox { Text = name, Checked = value, AutoSize = true, Margin = new Padding(0, 2, 0, 3) };
            panel.Controls.Add(box);
            return box;
        }

        private void FinishSmoke()
        {
            if (options.Screenshot != null)
            {
                using (var image = new Bitmap(Width, Height))
                {
                    DrawToBitmap(image, new Rectangle(Point.Empty, Size));
                    using (var graphics = Graphics.FromImage(image))
                    {
                        var active = inspector.Visible ? inspector : runtime.Visible ? runtime : null;
                        if (active != null)
                        {
                            var origin = PointToClient(active.PointToScreen(Point.Empty));
                            int captionHeight = PointToScreen(Point.Empty).Y - PointToScreen(new Point(0, -1)).Y;
                            var bounds = new Rectangle(origin.X + SystemInformation.FrameBorderSize.Width + 1,
                                origin.Y + SystemInformation.CaptionHeight + SystemInformation.FrameBorderSize.Height
                                + captionHeight, active.Width, active.Height);
                            TextRenderer.DrawText(graphics, active.Text, active.Font, bounds, active.ForeColor,
                                TextFormatFlags.Top | TextFormatFlags.Left | TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
                        }
                    }
                    image.Save(options.Screenshot, ImageFormat.Png);
                }
            }
            if (options.PerfOutput != null)
            {
                using (var process = Process.GetCurrentProcess())
                {
                    double elapsed = benchmark.Elapsed.TotalSeconds;
                    double measured = Math.Max(.01, elapsed - sampleAt);
                    var observer = connection == null ? new KeyValuePair<double, long>() : connection.Metrics();
                    double desktopCpu = process.TotalProcessorTime.TotalSeconds - firstCpu;
                    double observerCpu = Math.Max(0, observer.Key - firstObserverCpu);
                    var data = new Dictionary<string, object> { { "elapsedSeconds", elapsed },
                        { "measurementSeconds", measured },
                        { "cpuOfMachinePercent", desktopCpu * 100 / measured / Environment.ProcessorCount },
                        { "combinedCpuOfMachinePercent", (desktopCpu + observerCpu) * 100 / measured / Environment.ProcessorCount },
                        { "paintedFramesDuringMeasurement", topology.PaintedFrames - firstPaintedFrames },
                        { "animatedFramesDuringMeasurement", topology.AnimatedFrames - firstAnimatedFrames },
                        { "maxWorkingSetBytes", maxWorkingSet }, { "paintedFrames", topology.PaintedFrames },
                        { "maxCombinedWorkingSetBytes", maxCombinedWorkingSet },
                        { "animatedFrames", topology.AnimatedFrames }, { "observedSessions", last.Sessions.Count },
                        { "observedEvents", last.Events.Count }, { "mode", CurrentMode() },
                        { "selectedEvents", displayed.Count }, { "selectedSession", SelectedSession == null ? null : SelectedSession.Id },
                        { "securityDenials", displayed.Count(x => x.IsSecurity && (x.Verdict == "DENY" || x.Status == "DENIED")) },
                        { "activeNodesAtExit", graph.Count(x => x.Active) },
                        { "logicalCpus", Environment.ProcessorCount } };
                    Wire.WriteAtomic(options.PerfOutput, Wire.Json().Serialize(data));
                }
            }
            Exit();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                pulse.Dispose();
                tray.Dispose();
                StopConnection();
            }
            base.Dispose(disposing);
        }

        private sealed class Choice
        {
            internal static readonly Choice Overview = new Choice("", "");
            internal readonly string Key;
            internal readonly SessionInfo Info;
            private readonly string label;
            internal Choice(string key, string label, SessionInfo info = null) { Key = key; this.label = label; Info = info; }
            public override string ToString() { return label; }
        }

        private sealed class EventRow
        {
            internal readonly Observation Event;
            internal EventRow(Observation item) { Event = item; }
            public override string ToString() { return Event.Timestamp.ToLocalTime().ToString("HH:mm:ss")
                + "  " + Event.Family.ToUpperInvariant() + "  " + Event.Subject + "  " + Event.Status; }
        }

        private sealed class SkillRow
        {
            internal readonly string Name, Status;
            internal readonly Observation Event;
            internal SkillRow(string name, string status, Observation entry) { Name = name; Status = status; Event = entry; }
            public override string ToString() { return Name + "   " + Status.Replace('_', ' '); }
        }
    }
}
