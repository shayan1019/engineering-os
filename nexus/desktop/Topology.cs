using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Linq;
using System.Windows.Forms;

namespace Eos.Nexus
{
    internal static class Palette
    {
        internal static readonly Color Obsidian = Color.FromArgb(7, 12, 22);
        internal static readonly Color Panel = Color.FromArgb(13, 23, 37);
        internal static readonly Color Edge = Color.FromArgb(38, 58, 77);
        internal static readonly Color Text = Color.FromArgb(220, 235, 247);
        internal static readonly Color Muted = Color.FromArgb(154, 176, 195);
        internal static readonly Color Cyan = Color.FromArgb(75, 214, 240);
        internal static readonly Color Violet = Color.FromArgb(181, 145, 249);
        internal static readonly Color Green = Color.FromArgb(99, 230, 174);
        internal static readonly Color Red = Color.FromArgb(255, 117, 134);
        internal static readonly Color Amber = Color.FromArgb(255, 205, 119);
        internal static readonly Font Body = new Font("Segoe UI", 9.0f);
        internal static readonly Font Small = new Font("Segoe UI", 8.0f);
        internal static readonly Font Label = new Font("Segoe UI Semibold", 9.0f);
        internal static readonly Font Mono = new Font("Consolas", 9.0f);
        internal static readonly Font Title = new Font("Segoe UI Semibold", 13.0f);
        internal static readonly Font Core = new Font("Segoe UI Semibold", 36.0f);

        internal static Color With(Color color, int alpha) { return Color.FromArgb(alpha, color); }
        internal static Color Status(string status)
        {
            if (status == "DENIED" || status == "DENY" || status == "FAIL" || status == "FAILED") return Red;
            if (status == "PASS") return Green;
            if (status == "BLOCKED" || status == "STALE") return Amber;
            if (status == "ACTIVE" || status == "IN_USE" || status == "LIVE") return Cyan;
            if (status == "LOADED" || status == "SELECTED") return Violet;
            return Muted;
        }
    }

    internal sealed class GraphNode
    {
        internal string Key, Name, Status, Parent;
        internal Color Accent;
        internal bool Active;
        internal List<Observation> Evidence;
    }

    internal sealed class Topology : Control
    {
        private readonly Stopwatch time = Stopwatch.StartNew();
        private readonly Dictionary<string, PointF> moved = new Dictionary<string, PointF>();
        private readonly Dictionary<string, RectangleF> hits = new Dictionary<string, RectangleF>();
        private readonly Timer clock;
        private List<GraphNode> nodes = new List<GraphNode>();
        private PointF pan;
        private Point last;
        private string dragging;
        private Point mouseDown;
        private float zoom = 1;
        private bool motion, lowResource, flow, active;
        private string selected = "core";
        private string subtitle = "";
        private Bitmap background;
        private Dictionary<string, PointF> anchors;
        private Rectangle lastMotion;
        private bool needsRedraw = true;
        internal long PaintedFrames { get; private set; }
        internal long AnimatedFrames { get; private set; }
        internal event Action<string> Selected;

        internal Topology()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer
                | ControlStyles.UserPaint | ControlStyles.ResizeRedraw | ControlStyles.Selectable, true);
            BackColor = Palette.Obsidian;
            ForeColor = Palette.Text;
            TabStop = true;
            AccessibleName = "EOS NEXUS topology";
            clock = new Timer { Interval = 15 };
            clock.Tick += (sender, args) => NextFrame();
            VisibleChanged += (sender, args) => UpdateClock();
        }

        internal void Render(List<GraphNode> data, bool animate, bool low, bool workflow, string caption)
        {
            nodes = data;
            motion = animate;
            lowResource = low;
            flow = workflow;
            subtitle = caption;
            active = nodes.Any(x => x.Active);
            UpdateClock();
            RedrawScene();
        }

        internal void SuspendMotion()
        {
            motion = false;
            UpdateClock();
        }

        private void UpdateClock()
        {
            clock.Interval = lowResource ? 50 : 15;
            bool enable = motion && active && Visible;
            if (enable != clock.Enabled)
            {
                clock.Enabled = enable;
                RedrawScene();
            }
        }

        internal void Fit()
        {
            pan = PointF.Empty;
            zoom = 1;
            moved.Clear();
            RedrawScene();
        }

        internal void Zoom(float factor)
        {
            zoom = Math.Max(.55f, Math.Min(2.7f, zoom * factor));
            RedrawScene();
        }

        internal void FocusNode(string key)
        {
            selected = key;
            RedrawScene();
        }

        protected override void OnPaint(PaintEventArgs args)
        {
            base.OnPaint(args);
            PaintedFrames++;
            if (clock.Enabled) AnimatedFrames++;
            if (Width < 10 || Height < 10) return;
            if (background == null || background.Width != Width || background.Height != Height)
            {
                if (background != null) background.Dispose();
                background = new Bitmap(Width, Height, System.Drawing.Imaging.PixelFormat.Format32bppPArgb);
                needsRedraw = true;
            }
            if (needsRedraw)
            {
                using (var layer = Graphics.FromImage(background)) DrawScene(layer);
                needsRedraw = false;
            }
            var clip = Rectangle.Intersect(args.ClipRectangle, ClientRectangle);
            if (clip.IsEmpty) return;
            args.Graphics.CompositingMode = CompositingMode.SourceCopy;
            args.Graphics.DrawImage(background, clip, clip, GraphicsUnit.Pixel);
            args.Graphics.CompositingMode = CompositingMode.SourceOver;
            if (clock.Enabled) DrawMotion(args.Graphics);
        }

        protected override void OnPaintBackground(PaintEventArgs args) { }

        private void RedrawScene()
        {
            needsRedraw = true;
            lastMotion = Rectangle.Empty;
            Invalidate();
        }

        private void NextFrame()
        {
            if (needsRedraw || anchors == null) { Invalidate(); return; }
            float radius = (flow ? 86 : 95) + (lowResource ? 1 : 32);
            var current = ScreenBounds(Circle(anchors["core"], radius));
            double seconds = time.Elapsed.TotalSeconds;
            foreach (var node in nodes)
            {
                if (node.Key == "core" || !node.Active) continue;
                PointF packet;
                if (PacketPosition(EdgeOrigin(node), anchors[node.Key], node, seconds, out packet))
                    current = Rectangle.Union(current, ScreenBounds(Circle(packet, 9)));
            }
            var invalid = lastMotion.IsEmpty ? current : Rectangle.Union(current, lastMotion);
            lastMotion = current;
            Invalidate(Rectangle.Intersect(invalid, ClientRectangle));
        }

        private Rectangle ScreenBounds(RectangleF area)
        {
            float scale = WorldScale();
            float x = Width / 2f + pan.X;
            float y = Height / 2f + pan.Y;
            return Rectangle.FromLTRB((int)Math.Floor(x + area.Left * scale) - 2,
                (int)Math.Floor(y + area.Top * scale) - 2, (int)Math.Ceiling(x + area.Right * scale) + 2,
                (int)Math.Ceiling(y + area.Bottom * scale) + 2);
        }

        private void PrepareWorld(Graphics graphics)
        {
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            graphics.TranslateTransform(Width / 2f + pan.X, Height / 2f + pan.Y);
            float scale = WorldScale();
            graphics.ScaleTransform(scale, scale);
        }

        private void DrawScene(Graphics graphics)
        {
            graphics.Clear(BackColor);
            DrawGrid(graphics);
            PrepareWorld(graphics);
            PointF center = flow ? new PointF(-315, 10) : new PointF(0, 4);
            PointF router = flow ? new PointF(-315, -174) : new PointF(0, -199);
            hits.Clear();

            if (!flow)
            {
                using (var orbit = new Pen(Palette.With(Palette.Cyan, 41)))
                {
                    graphics.DrawEllipse(orbit, -362, -206, 724, 429);
                    orbit.DashStyle = DashStyle.Dot;
                    graphics.DrawEllipse(orbit, -326, -180, 652, 375);
                    graphics.DrawEllipse(orbit, -144, -140, 288, 288);
                }
            }

            anchors = LayoutNodes(center, router);
            foreach (var node in nodes.Where(x => x.Key != "core"))
                DrawEdge(graphics, EdgeOrigin(node), anchors[node.Key], node);
            DrawCore(graphics, anchors["core"]);
            foreach (var node in nodes.Where(x => x.Key != "core"))
                DrawNode(graphics, anchors[node.Key], node);
            graphics.ResetTransform();
            using (var muted = new SolidBrush(Palette.Muted))
                graphics.DrawString(subtitle, Palette.Small, muted, 12, Height - 21);
        }

        private PointF EdgeOrigin(GraphNode node)
        {
            PointF start;
            return node.Parent != null && node.Parent != node.Key && anchors.TryGetValue(node.Parent, out start)
                ? start : anchors["core"];
        }

        private void DrawMotion(Graphics graphics)
        {
            double seconds = time.Elapsed.TotalSeconds;
            PrepareWorld(graphics);
            var point = anchors["core"];
            float radius = flow ? 86 : 95;
            float angle = (float)(seconds * 27 % 360);
            DrawRing(graphics, point, radius - 7, Palette.Cyan, angle, 35);
            DrawRing(graphics, point, radius - 15, Palette.Violet, -angle * 1.4f, 48);
            if (!lowResource)
            {
                float progress = (float)(seconds % 2.5 / 2.5);
                using (var pulse = new Pen(Palette.With(Palette.Cyan, (int)(105 * (1 - progress)))))
                    graphics.DrawEllipse(pulse, Circle(point, radius + progress * 31));
            }
            foreach (var node in nodes)
                if (node.Key != "core" && node.Active)
                    DrawPacket(graphics, EdgeOrigin(node), anchors[node.Key], node, seconds);
            graphics.ResetTransform();
        }

        private void DrawGrid(Graphics graphics)
        {
            using (var brush = new SolidBrush(Color.FromArgb(33, 53, 72)))
                for (int y = 13; y < Height; y += 18)
                    for (int x = 13; x < Width; x += 18)
                        graphics.FillRectangle(brush, x, y, 1, 1);
        }

        private Dictionary<string, PointF> LayoutNodes(PointF center, PointF router)
        {
            var anchors = new Dictionary<string, PointF> { { "core", center }, { "router", router } };
            var outer = nodes.Where(x => x.Key != "router" && x.Key != "core").ToList();
            int half = (outer.Count + 1) / 2;
            for (int index = 0; index < outer.Count; index++)
            {
                int side = index < half ? 0 : 1;
                int row = index - side * half;
                int count = Math.Max(1, side == 0 ? half : outer.Count - half);
                float spacing = Math.Min(56, 400f / count);
                float y = (row - (count - 1) / 2f) * spacing + 18;
                float x = flow ? 16 + side * 251 : (side == 0 ? -312 : 312);
                anchors[outer[index].Key] = new PointF(x, y);
            }
            foreach (string key in anchors.Keys.ToList())
            {
                PointF custom;
                if (moved.TryGetValue(key, out custom)) anchors[key] = custom;
            }
            return anchors;
        }

        private void DrawEdge(Graphics graphics, PointF from, PointF to, GraphNode node)
        {
            float x = to.X - from.X;
            PointF a = new PointF(from.X + x * .44f, from.Y);
            PointF b = new PointF(to.X - x * .44f, to.Y);
            bool emphasized = node.Active || node.Key == selected;
            using (var pen = new Pen(Palette.With(node.Accent, emphasized ? 179 : 62), emphasized ? 1.8f : 1.05f))
            {
                if (!emphasized) pen.DashStyle = DashStyle.Dash;
                graphics.DrawBezier(pen, from, a, b, to);
            }
        }

        private static bool PacketPosition(PointF from, PointF to, GraphNode node, double seconds, out PointF point)
        {
            float x = to.X - from.X;
            PointF a = new PointF(from.X + x * .44f, from.Y);
            PointF b = new PointF(to.X - x * .44f, to.Y);
            float t = (float)((seconds * .29 + (uint)node.Key.GetHashCode() % 73 / 73.0) % 1);
            // Hide the endpoints while packets pass beneath the node cards.
            point = PointF.Empty;
            if (t < .21 || t > .83) return false;
            float u = 1 - t;
            float px = u * u * u * from.X + 3 * u * u * t * a.X + 3 * u * t * t * b.X + t * t * t * to.X;
            float py = u * u * u * from.Y + 3 * u * u * t * a.Y + 3 * u * t * t * b.Y + t * t * t * to.Y;
            point = new PointF(px, py);
            return true;
        }

        private static void DrawPacket(Graphics graphics, PointF from, PointF to, GraphNode node, double seconds)
        {
            PointF point;
            if (!PacketPosition(from, to, node, seconds, out point)) return;
            using (var glow = new SolidBrush(Palette.With(node.Accent, 54)))
            using (var packet = new SolidBrush(node.Accent))
            {
                graphics.FillEllipse(glow, point.X - 8, point.Y - 8, 16, 16);
                graphics.FillEllipse(packet, point.X - 3, point.Y - 3, 6, 6);
            }
        }

        private void DrawCore(Graphics graphics, PointF point)
        {
            var core = nodes.FirstOrDefault(x => x.Key == "core");
            float radius = flow ? 86 : 95;
            hits["core"] = Circle(point, radius);
            if (!lowResource)
                for (int index = 4; index > 0; index--)
                    using (var glow = new SolidBrush(Palette.With(Palette.Cyan, 6)))
                        graphics.FillEllipse(glow, Circle(point, radius + index * 12));
            using (var outer = new SolidBrush(Color.FromArgb(9, 28, 40)))
            using (var inner = new SolidBrush(Color.FromArgb(12, 41, 56)))
            using (var border = new Pen(Palette.With(Palette.Cyan, 207), 1.5f))
            {
                graphics.FillEllipse(outer, Circle(point, radius));
                graphics.DrawEllipse(border, Circle(point, radius));
                graphics.FillEllipse(inner, Circle(point, radius - 27));
                graphics.DrawEllipse(border, Circle(point, radius - 27));
            }

            if (!clock.Enabled)
            {
                DrawRing(graphics, point, radius - 7, Palette.Cyan, 0, 35);
                DrawRing(graphics, point, radius - 15, Palette.Violet, 0, 48);
            }
            TextAt(graphics, "EOS CORE", Palette.Label, Palette.Muted, point.X, point.Y - 35);
            TextAt(graphics, "CEO", Palette.Core, Palette.Cyan, point.X, point.Y - 4);
            TextAt(graphics, core == null ? "UNKNOWN" : core.Status, Palette.Mono,
                core == null ? Palette.Muted : Palette.Status(core.Status), point.X, point.Y + 42);
        }

        private static void DrawRing(Graphics graphics, PointF point, float radius, Color color, float angle, float sweep)
        {
            using (var pen = new Pen(color, 2.3f))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                for (int index = 0; index < 4; index++)
                    graphics.DrawArc(pen, Circle(point, radius), angle + index * 90, sweep);
            }
        }

        private void DrawNode(Graphics graphics, PointF point, GraphNode node)
        {
            bool emphasized = node.Active || node.Key == selected;
            float width = flow ? 184 : 216;
            var bounds = new RectangleF(point.X - width / 2, point.Y - 20, width, 40);
            hits[node.Key] = bounds;
            using (var path = Rounded(bounds, 7))
            using (var fill = new SolidBrush(emphasized ? Color.FromArgb(17, 38, 54) : Palette.Panel))
            using (var pen = new Pen(Palette.With(node.Accent, emphasized ? 235 : 123), emphasized ? 1.6f : 1))
            using (var dot = new SolidBrush(node.Active ? Palette.Cyan : Palette.With(node.Accent, 146)))
            {
                graphics.FillPath(fill, path);
                graphics.DrawPath(pen, path);
                graphics.FillEllipse(dot, bounds.Left + 11, bounds.Top + 10, 6, 6);
            }
            TextAt(graphics, node.Name, Palette.Label, Palette.Text, point.X, point.Y - 6, width - 35);
            TextAt(graphics, node.Status, Palette.Small, Palette.Status(node.Status), point.X, point.Y + 10, width - 24);
        }

        private static GraphicsPath Rounded(RectangleF rectangle, float radius)
        {
            float diameter = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(rectangle.Left, rectangle.Top, diameter, diameter, 180, 90);
            path.AddArc(rectangle.Right - diameter, rectangle.Top, diameter, diameter, 270, 90);
            path.AddArc(rectangle.Right - diameter, rectangle.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(rectangle.Left, rectangle.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }

        private static void TextAt(Graphics graphics, string value, Font font, Color color, float x, float y, float width = 170)
        {
            using (var brush = new SolidBrush(color))
            using (var style = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center,
                Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
                graphics.DrawString(value, font, brush, new RectangleF(x - width / 2, y - 29, width, 58), style);
        }

        private static RectangleF Circle(PointF point, float radius)
        {
            return new RectangleF(point.X - radius, point.Y - radius, radius * 2, radius * 2);
        }

        private float WorldScale() { return Math.Max(.1f, Math.Min(Width / 872f, Height / 500f)) * zoom; }
        private PointF World(Point point)
        {
            float scale = WorldScale();
            return new PointF((point.X - Width / 2f - pan.X) / scale, (point.Y - Height / 2f - pan.Y) / scale);
        }

        protected override void OnMouseDown(MouseEventArgs args)
        {
            base.OnMouseDown(args);
            Focus();
            last = mouseDown = args.Location;
            var point = World(args.Location);
            dragging = args.Button == MouseButtons.Left
                ? hits.LastOrDefault(x => x.Value.Contains(point)).Key : null;
            if (args.Button == MouseButtons.Left || args.Button == MouseButtons.Middle) Capture = true;
            if (dragging != null && Selected != null) Selected(dragging);
            else if (args.Button == MouseButtons.Left && Selected != null)
            {
                // Edge hit test is against the same Bézier shape as the renderer.
                foreach (var node in nodes.Where(x => x.Key != "core"))
                {
                    RectangleF target, origin;
                    if (!hits.TryGetValue(node.Key, out target) || !hits.TryGetValue(node.Parent ?? "core", out origin)) continue;
                    var from = new PointF(origin.Left + origin.Width / 2, origin.Top + origin.Height / 2);
                    var to = new PointF(target.Left + target.Width / 2, target.Top + target.Height / 2);
                    float delta = (to.X - from.X) * .44f;
                    using (var path = new GraphicsPath())
                    using (var outline = new Pen(Color.Black, 12))
                    {
                        path.AddBezier(from, new PointF(from.X + delta, from.Y), new PointF(to.X - delta, to.Y), to);
                        if (path.IsOutlineVisible(point, outline)) { Selected(node.Key); break; }
                    }
                }
            }
        }

        protected override void OnMouseMove(MouseEventArgs args)
        {
            base.OnMouseMove(args);
            if (!Capture) return;
            if (dragging == null)
            {
                pan.X += args.X - last.X;
                pan.Y += args.Y - last.Y;
            }
            else if (Math.Abs(args.X - mouseDown.X) + Math.Abs(args.Y - mouseDown.Y) > 4)
                moved[dragging] = World(args.Location);
            last = args.Location;
            RedrawScene();
        }

        protected override void OnMouseUp(MouseEventArgs args)
        {
            base.OnMouseUp(args);
            Capture = false;
            dragging = null;
        }

        protected override void OnMouseWheel(MouseEventArgs args)
        {
            base.OnMouseWheel(args);
            Zoom(args.Delta > 0 ? 1.14f : 1 / 1.14f);
        }

        protected override bool IsInputKey(Keys keyData)
        {
            return keyData == Keys.Left || keyData == Keys.Right || keyData == Keys.Up || keyData == Keys.Down
                || base.IsInputKey(keyData);
        }

        protected override void OnKeyDown(KeyEventArgs args)
        {
            if (args.KeyCode == Keys.Left) pan.X -= 22;
            else if (args.KeyCode == Keys.Right) pan.X += 22;
            else if (args.KeyCode == Keys.Up) pan.Y -= 22;
            else if (args.KeyCode == Keys.Down) pan.Y += 22;
            else { base.OnKeyDown(args); return; }
            args.Handled = true;
            RedrawScene();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                clock.Dispose();
                if (background != null) background.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    internal sealed class Radar : Control
    {
        private List<Observation> observations = new List<Observation>();

        internal Radar()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                ControlStyles.ResizeRedraw | ControlStyles.UserPaint, true);
            BackColor = Palette.Panel;
            AccessibleName = "Security event radar";
        }

        internal void Update(List<Observation> events)
        {
            observations = events;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs args)
        {
            base.OnPaint(args);
            var graphics = args.Graphics;
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            float radius = Math.Max(2, Math.Min(Width, Height) / 2f - 7);
            float x = Width / 2f, y = Height / 2f;
            using (var fill = new SolidBrush(Color.FromArgb(8, 25, 23)))
            using (var tick = new Pen(Palette.With(Palette.Green, 76)))
            {
                graphics.FillEllipse(fill, x - radius, y - radius, radius * 2, radius * 2);
                for (int index = 1; index <= 3; index++)
                {
                    float r = radius * index / 3;
                    graphics.DrawEllipse(tick, x - r, y - r, 2 * r, 2 * r);
                }
                graphics.DrawLine(tick, x - radius, y, x + radius, y);
                graphics.DrawLine(tick, x, y - radius, x, y + radius);
            }
            var recent = observations.Reverse<Observation>().Take(30).ToArray();
            for (int i = 0; i < recent.Length; i++)
            {
                var status = recent[i].Verdict.Length > 0 ? recent[i].Verdict : recent[i].Status;
                double angle = (uint)recent[i].Id.GetHashCode() % 360 * Math.PI / 180;
                float r = radius * ((i % 3) + 1) / 4;
                using (var signal = new SolidBrush(Palette.Status(status)))
                    graphics.FillEllipse(signal, x + (float)Math.Cos(angle) * r - 2.6f,
                        y + (float)Math.Sin(angle) * r - 2.6f, 5.2f, 5.2f);
            }
        }
    }
}
