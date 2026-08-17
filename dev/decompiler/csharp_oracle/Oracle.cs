// Rotation oracle — answers "which source quadrant lands where on the wire?"
// for a given panel, encoder and display angle, under mono + libgdiplus.
//
//   Args:  <w> <h> <jpeg 0|1> <pm> <dir>
//   Emits: WxH TL,TR,BL,BR      e.g. "240x320 B,R,Y,G"
//
// WHY THIS EXISTS
// Our renderer must land each corner of the composed image on the same physical
// corner of the glass as the Windows app does. Reasoning about that from the
// rotation tables alone is error-prone — a transposed pair reads fine on paper
// and is upside-down on the panel. Running it and reading the quadrants back is
// not.
//
// WHAT THIS IS NOT
// `Rotate` below is written from the *described* behaviour of the vendor's
// rotation step, not copied from it: expand the canvas to the rotated bounding
// box, draw the source centred, high-quality resampling. That is deliberate.
// An independent implementation agreeing with our Python is evidence; a pasted
// copy agreeing with itself is not. It also keeps decompiled source out of this
// repository — see AUDIT_INDEX.md "Provenance".
//
// The angle tables in `WireAngle` are DATA recovered from the encoder's
// per-resolution switch, and they are the same values `src/trcc/core/protocol.py`
// carries. If the two ever disagree, one of them is wrong and this is how you
// find out which.
//
//   mcs Oracle.cs -r:System.Drawing && mono Oracle.exe 320 240 0 51 90
using System;
using System.Drawing;
using System.Drawing.Drawing2D;

class Oracle {
    // Rotate about the centre, growing the canvas to fit the rotated bounds.
    // For the multiples of 90 this tool is used with, the bounding box is exact
    // and the result is a lossless quadrant permutation.
    public static Image Rotate(Image img, float angle) {
        double rad = angle * Math.PI / 180.0;
        double cos = Math.Abs(Math.Cos(rad)), sin = Math.Abs(Math.Sin(rad));
        int w = img.Width, h = img.Height;
        int outW = (int)Math.Round(w * cos + h * sin);
        int outH = (int)Math.Round(w * sin + h * cos);

        Bitmap dst = new Bitmap(outW, outH);
        using (Graphics g = Graphics.FromImage(dst)) {
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.SmoothingMode = SmoothingMode.HighQuality;
            g.TranslateTransform(outW / 2f, outH / 2f);
            g.RotateTransform(angle);
            g.DrawImage(img, -w / 2f, -h / 2f, w, h);
        }
        return dst;
    }

    static float Ang(int d, float a0, float a90, float a180, float a270) {
        return d == 0 ? a0 : d == 90 ? a90 : d == 180 ? a180 : a270;
    }

    // Recovered angle tables, keyed by encoder + resolution (+ pm where it
    // disambiguates).  Cited: ImageToJpg / ImageTo565 in FormCZTV.cs.
    static float WireAngle(int w, int h, bool jpeg, int pm, int d) {
        if (jpeg) {
            if ((w == 320 && h == 320) || (w == 480 && h == 480))
                return pm == 6 ? Ang(d, 180, 90, 0, 270) : Ang(d, 0, 270, 180, 90);
            if (pm == 5) return Ang(d, 0, 270, 180, 90);
            if (w == 1600 && h == 720) return Ang(d, 180, 90, 0, 270);
            if (((w == 1280 || w == 800 || w == 854) && h == 480) || (w == 960 && h == 540))
                return Ang(d, 0, 270, 180, 90);
            if (w == 1920 && h == 462) return Ang(d, 180, 90, 0, 270);
            if (w == 640 && h == 480) return Ang(d, 0, 270, 180, 90);
            return Ang(d, 90, 0, 270, 180);
        }
        if ((w == 240 && h == 240) || (w == 320 && h == 320) || (w == 480 && h == 480))
            return Ang(d, 0, 270, 180, 90);
        return Ang(d, 90, 0, 270, 180);
    }

    // Four solid quadrants, so the output names where each corner travelled.
    static Bitmap Pattern(int w, int h) {
        var b = new Bitmap(w, h);
        using (var g = Graphics.FromImage(b)) {
            g.FillRectangle(new SolidBrush(Color.FromArgb(255, 0, 0)), 0, 0, w / 2, h / 2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(0, 255, 0)), w / 2, 0, w / 2, h / 2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(0, 0, 255)), 0, h / 2, w / 2, h / 2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(255, 255, 0)), w / 2, h / 2, w / 2, h / 2);
        }
        return b;
    }

    static string Q(Color c) =>
        c.R > 150 && c.G < 100 && c.B < 100 ? "R" :
        c.G > 150 && c.R < 100 && c.B < 100 ? "G" :
        c.B > 150 && c.R < 100 && c.G < 100 ? "B" :
        c.R > 150 && c.G > 150 && c.B < 100 ? "Y" : ".";

    static void Main(string[] a) {
        int w = int.Parse(a[0]), h = int.Parse(a[1]);
        bool jpeg = a[2] == "1";
        int pm = int.Parse(a[3]), d = int.Parse(a[4]);
        var o = (Bitmap)Rotate(Pattern(w, h), WireAngle(w, h, jpeg, pm, d));
        int W = o.Width, H = o.Height;
        Console.WriteLine(W + "x" + H + " "
            + Q(o.GetPixel(W / 4, H / 4)) + "," + Q(o.GetPixel(3 * W / 4, H / 4)) + ","
            + Q(o.GetPixel(W / 4, 3 * H / 4)) + "," + Q(o.GetPixel(3 * W / 4, 3 * H / 4)));
    }
}
