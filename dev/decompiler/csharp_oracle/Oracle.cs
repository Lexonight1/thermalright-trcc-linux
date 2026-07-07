// Executable C# rotation oracle — real decompiled RotateImg (TRCC.decompiled.cs
// :39916) under mono+libgdiplus.  Args: <w> <h> <jpeg 0|1> <pm> <dir>.
// Emits one line:  WxH TL,TR,BL,BR   (which source quadrant lands where).
using System;
using System.Drawing;
using System.Drawing.Drawing2D;

class Oracle {
    public static Image RotateImg(Image img, float angle) {   // VERBATIM :39916
        int width = img.Width; int height = img.Height;
        Matrix val = new Matrix();
        val.RotateAt(angle, new PointF(width/2, height/2), MatrixOrder.Append);
        GraphicsPath val2 = new GraphicsPath();
        val2.AddRectangle(new RectangleF(0f,0f,width,height));
        RectangleF bounds = val2.GetBounds(val);
        Bitmap val3 = new Bitmap((int)Math.Round(bounds.Width), (int)Math.Round(bounds.Height));
        Graphics val4 = Graphics.FromImage(val3);
        val4.InterpolationMode = InterpolationMode.HighQualityBicubic;
        val4.SmoothingMode = SmoothingMode.HighQuality;
        Point point = new Point((int)(bounds.Width-width)/2, (int)(bounds.Height-height)/2);
        Rectangle rectangle = new Rectangle(point.X, point.Y, width, height);
        Point p2 = new Point(rectangle.X+rectangle.Width/2, rectangle.Y+rectangle.Height/2);
        val4.TranslateTransform(p2.X, p2.Y); val4.RotateTransform(angle);
        val4.TranslateTransform(-p2.X, -p2.Y); val4.DrawImage(img, rectangle);
        val4.Dispose(); return val3;
    }
    static float Ang(int d,float a0,float a90,float a180,float a270){
        return d==0?a0: d==90?a90: d==180?a180: a270; }
    // angle values copied 1:1 from ImageToJpg(:65262)/ImageTo565(:65578) switches
    static float WireAngle(int w,int h,bool jpeg,int pm,int d){
        if(jpeg){
            if((w==320&&h==320)||(w==480&&h==480))
                return pm==6? Ang(d,180,90,0,270): Ang(d,0,270,180,90);
            if(pm==5) return Ang(d,0,270,180,90);
            if(w==1600&&h==720) return Ang(d,180,90,0,270);
            if(((w==1280||w==800||w==854)&&h==480)||(w==960&&h==540)) return Ang(d,0,270,180,90);
            if(w==1920&&h==462) return Ang(d,180,90,0,270);
            if(w==640&&h==480) return Ang(d,0,270,180,90);
            return Ang(d,90,0,270,180);
        }
        if((w==240&&h==240)||(w==320&&h==320)||(w==480&&h==480)) return Ang(d,0,270,180,90);
        return Ang(d,90,0,270,180);
    }
    static Bitmap Pattern(int w,int h){
        var b=new Bitmap(w,h);
        using(var g=Graphics.FromImage(b)){
            g.FillRectangle(new SolidBrush(Color.FromArgb(255,0,0)),0,0,w/2,h/2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(0,255,0)),w/2,0,w/2,h/2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(0,0,255)),0,h/2,w/2,h/2);
            g.FillRectangle(new SolidBrush(Color.FromArgb(255,255,0)),w/2,h/2,w/2,h/2);
        }
        return b;
    }
    static string Q(Color c)=>
        c.R>150&&c.G<100&&c.B<100?"R": c.G>150&&c.R<100&&c.B<100?"G":
        c.B>150&&c.R<100&&c.G<100?"B": c.R>150&&c.G>150&&c.B<100?"Y":".";
    static void Main(string[] a){
        int w=int.Parse(a[0]),h=int.Parse(a[1]); bool jpeg=a[2]=="1";
        int pm=int.Parse(a[3]),d=int.Parse(a[4]);
        var o=(Bitmap)RotateImg(Pattern(w,h), WireAngle(w,h,jpeg,pm,d));
        int W=o.Width,H=o.Height;
        Console.WriteLine(W+"x"+H+" "+Q(o.GetPixel(W/4,H/4))+","+Q(o.GetPixel(3*W/4,H/4))
            +","+Q(o.GetPixel(W/4,3*H/4))+","+Q(o.GetPixel(3*W/4,3*H/4)));
    }
}
