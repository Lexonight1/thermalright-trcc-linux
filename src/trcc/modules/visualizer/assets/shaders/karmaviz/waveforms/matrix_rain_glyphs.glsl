// KarmaViz waveform "matrix_rain_glyphs" (digital): Matrix rain effect using authentic Japanese katakana and symbols: モエヤキオカ7ケサスz152ヨタワ4ネヌナ98ヒ0ホア3ウ セ¦:"꞊ミラリ╌ツテニハソ▪—<>0|+*コシマムメ
// From KarmaViz 67e8a15 waveforms/digital/matrix_rain_glyphs.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.
// Matrix Rain with Specific Glyphs
// Glyphs: モエヤキオカ7ケサスz152ヨタワ4ネヌナ98ヒ0ホア3ウ セ¦:"꞊ミラリ╌ツテニハソ▪—<>0|+*コシマムメ

// Function to create specific glyph patterns
float get_glyph_intensity(float char_x, float char_y, float glyph_index) {
    float cx = char_x;
    float cy = char_y;
    
    // Map glyph_index to specific patterns (0-63 for 64 different glyphs)
    int glyph_id = int(mod(glyph_index * 64.0, 64.0));
    
    // Katakana and symbol patterns
    if (glyph_id == 0) { // モ (Mo)
        float v1 = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0; // Left vertical
        float v2 = (abs(cx - 0.8) < 0.05) ? 1.0 : 0.0; // Right vertical
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.85) ? 1.0 : 0.0; // Top horizontal
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.15 && cx < 0.85) ? 1.0 : 0.0; // Bottom horizontal
        return v1 + v2 + h1 + h2;
    }
    else if (glyph_id == 1) { // エ (E)
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return h1 + h2 + h3;
    }
    else if (glyph_id == 2) { // ヤ (Ya)
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.2) ? 1.0 : 0.0; // Center vertical
        float d1 = (abs((cx - 0.5) - (cy - 0.5) * 0.6) < 0.05 && cy < 0.5) ? 1.0 : 0.0; // Left diagonal
        float d2 = (abs((cx - 0.5) + (cy - 0.5) * 0.6) < 0.05 && cy < 0.5) ? 1.0 : 0.0; // Right diagonal
        return v + d1 + d2;
    }
    else if (glyph_id == 3) { // キ (Ki)
        float v = (abs(cx - 0.5) < 0.05) ? 1.0 : 0.0; // Center vertical
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return v + h1 + h2;
    }
    else if (glyph_id == 4) { // オ (O)
        float v1 = (abs(cx - 0.3) < 0.05) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.7) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        return v1 + v2 + h1 + h2 + h3;
    }
    else if (glyph_id == 5) { // カ (Ka)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.5) - (cy - 0.6) * 0.8) < 0.05 && cy > 0.4) ? 1.0 : 0.0;
        return v + h1 + d;
    }
    else if (glyph_id == 6) { // 7
        float h = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.8) + (cy - 0.2) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return h + d;
    }
    else if (glyph_id == 7) { // ケ (Ke)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        return v + h1 + h2;
    }
    else if (glyph_id == 8) { // サ (Sa)
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.7) < 0.05 && cy > 0.5) ? 1.0 : 0.0;
        return h1 + h2 + v;
    }
    else if (glyph_id == 9) { // ス (Su)
        float d1 = (abs((cx - 0.2) - (cy - 0.2) * 0.6) < 0.05) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.4) - (cy - 0.4) * 0.6) < 0.05) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 10) { // z
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.2) - (cy - 0.8) * 0.75) < 0.05 && cy > 0.2 && cy < 0.8) ? 1.0 : 0.0;
        return h1 + h2 + d;
    }
    else if (glyph_id == 11) { // 1
        float v = (abs(cx - 0.5) < 0.05) ? 1.0 : 0.0;
        float h = (abs(cy - 0.8) < 0.05 && cx > 0.3 && cx < 0.7) ? 1.0 : 0.0;
        return v + h;
    }
    else if (glyph_id == 12) { // 5
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v1 = (abs(cx - 0.2) < 0.05 && cy > 0.2 && cy < 0.5) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.8) < 0.05 && cy > 0.5 && cy < 0.8) ? 1.0 : 0.0;
        return h1 + h2 + h3 + v1 + v2;
    }
    else if (glyph_id == 13) { // 2
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v1 = (abs(cx - 0.8) < 0.05 && cy > 0.2 && cy < 0.5) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.2) < 0.05 && cy > 0.5 && cy < 0.8) ? 1.0 : 0.0;
        return h1 + h2 + h3 + v1 + v2;
    }
    else if (glyph_id == 14) { // ヨ (Yo)
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return h1 + h2 + h3;
    }
    else if (glyph_id == 15) { // タ (Ta)
        float h = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return h + v;
    }
    else if (glyph_id == 16) { // ワ (Wa)
        float v1 = (abs(cx - 0.3) < 0.05) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.7) < 0.05) ? 1.0 : 0.0;
        float h = (abs(cy - 0.3) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        return v1 + v2 + h;
    }
    else if (glyph_id == 17) { // 4
        float v1 = (abs(cx - 0.3) < 0.05 && cy < 0.6) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.7) < 0.05) ? 1.0 : 0.0;
        float h = (abs(cy - 0.5) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        return v1 + v2 + h;
    }
    else if (glyph_id == 18) { // ネ (Ne)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.6) < 0.05 && cx > 0.4 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.6) - (cy - 0.7) * 0.5) < 0.05 && cy > 0.6) ? 1.0 : 0.0;
        return v + h1 + h2 + d;
    }
    else if (glyph_id == 19) { // ヌ (Nu)
        float d1 = (abs((cx - 0.2) - (cy - 0.2) * 0.8) < 0.05) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.8) + (cy - 0.2) * 0.8) < 0.05) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 20) { // ナ (Na)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.5) - (cy - 0.6) * 0.6) < 0.05 && cy > 0.4) ? 1.0 : 0.0;
        return v + h + d;
    }
    else if (glyph_id == 21) { // 9
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v1 = (abs(cx - 0.2) < 0.05 && cy > 0.2 && cy < 0.5) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.8) < 0.05) ? 1.0 : 0.0;
        return h1 + h2 + h3 + v1 + v2;
    }
    else if (glyph_id == 22) { // 8
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v1 = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.8) < 0.05) ? 1.0 : 0.0;
        return h1 + h2 + h3 + v1 + v2;
    }
    else if (glyph_id == 23) { // ヒ (Hi)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.6) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        return v + h1 + h2;
    }
    else if (glyph_id == 24) { // 0
        float v1 = (abs(cx - 0.3) < 0.05) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.7) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.8) < 0.05 && cx > 0.25 && cx < 0.75) ? 1.0 : 0.0;
        return v1 + v2 + h1 + h2;
    }
    else if (glyph_id == 25) { // ホ (Ho)
        float v = (abs(cx - 0.5) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return v + h1 + h2;
    }
    else if (glyph_id == 26) { // ア (A)
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.3) ? 1.0 : 0.0;
        float h = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float d1 = (abs((cx - 0.5) - (cy - 0.3) * 0.6) < 0.05 && cy < 0.3) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) + (cy - 0.3) * 0.6) < 0.05 && cy < 0.3) ? 1.0 : 0.0;
        return v + h + d1 + d2;
    }
    else if (glyph_id == 27) { // 3
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.8) < 0.05) ? 1.0 : 0.0;
        return h1 + h2 + h3 + v;
    }
    else if (glyph_id == 28) { // ウ (U)
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.6) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.6) ? 1.0 : 0.0;
        return h1 + h2 + v;
    }
    else if (glyph_id == 29) { // セ (Se)
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.7) < 0.05 && cy > 0.5) ? 1.0 : 0.0;
        return h1 + h2 + v;
    }
    else if (glyph_id == 30) { // ¦ (broken bar)
        float v1 = (abs(cx - 0.5) < 0.05 && cy > 0.1 && cy < 0.4) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.5) < 0.05 && cy > 0.6 && cy < 0.9) ? 1.0 : 0.0;
        return v1 + v2;
    }
    else if (glyph_id == 31) { // : (colon)
        float d1 = step(distance(vec2(cx, cy), vec2(0.5, 0.3)), 0.08);
        float d2 = step(distance(vec2(cx, cy), vec2(0.5, 0.7)), 0.08);
        return d1 + d2;
    }
    else if (glyph_id == 32) { // " (quote)
        float v1 = (abs(cx - 0.4) < 0.03 && cy > 0.1 && cy < 0.3) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.6) < 0.03 && cy > 0.1 && cy < 0.3) ? 1.0 : 0.0;
        return v1 + v2;
    }
    else if (glyph_id == 33) { // ꞊ (double hyphen)
        float h1 = (abs(cy - 0.4) < 0.03 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.6) < 0.03 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return h1 + h2;
    }
    else if (glyph_id == 34) { // ミ (Mi)
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.7) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h3 = (abs(cy - 0.8) < 0.05 && cx > 0.2 && cx < 0.7) ? 1.0 : 0.0;
        return h1 + h2 + h3;
    }
    else if (glyph_id == 35) { // ラ (Ra)
        float v = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h = (abs(cy - 0.3) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float d = (abs((cx - 0.8) + (cy - 0.3) * 0.8) < 0.05 && cy > 0.3) ? 1.0 : 0.0;
        return v + h + d;
    }
    else if (glyph_id == 36) { // リ (Ri)
        float v1 = (abs(cx - 0.3) < 0.05) ? 1.0 : 0.0;
        float v2 = (abs(cx - 0.7) < 0.05) ? 1.0 : 0.0;
        return v1 + v2;
    }
    else if (glyph_id == 37) { // ╌ (dashed line)
        float d1 = (abs(cy - 0.5) < 0.05 && cx > 0.1 && cx < 0.3) ? 1.0 : 0.0;
        float d2 = (abs(cy - 0.5) < 0.05 && cx > 0.4 && cx < 0.6) ? 1.0 : 0.0;
        float d3 = (abs(cy - 0.5) < 0.05 && cx > 0.7 && cx < 0.9) ? 1.0 : 0.0;
        return d1 + d2 + d3;
    }
    else if (glyph_id == 38) { // ツ (Tsu)
        float d1 = (abs((cx - 0.3) - (cy - 0.4) * 0.4) < 0.05 && cy > 0.2 && cy < 0.6) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.7) - (cy - 0.4) * 0.4) < 0.05 && cy > 0.2 && cy < 0.6) ? 1.0 : 0.0;
        float h = (abs(cy - 0.7) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return d1 + d2 + h;
    }
    else if (glyph_id == 39) { // テ (Te)
        float h = (abs(cy - 0.2) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return h + v;
    }
    else if (glyph_id == 40) { // ニ (Ni)
        float h1 = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.7) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return h1 + h2;
    }
    else if (glyph_id == 41) { // ハ (Ha)
        float d1 = (abs((cx - 0.5) - (cy - 0.2) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) + (cy - 0.2) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 42) { // ソ (So)
        float d1 = (abs((cx - 0.3) - (cy - 0.3) * 0.5) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.7) - (cy - 0.3) * 0.5) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 43) { // ▪ (black square)
        return (cx > 0.3 && cx < 0.7 && cy > 0.3 && cy < 0.7) ? 1.0 : 0.0;
    }
    else if (glyph_id == 44) { // — (em dash)
        return (abs(cy - 0.5) < 0.05 && cx > 0.1 && cx < 0.9) ? 1.0 : 0.0;
    }
    else if (glyph_id == 45) { // < (less than)
        float d1 = (abs((cx - 0.7) + (cy - 0.5) * 0.8) < 0.05 && cy < 0.5) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.7) - (cy - 0.5) * 0.8) < 0.05 && cy > 0.5) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 46) { // > (greater than)
        float d1 = (abs((cx - 0.3) - (cy - 0.5) * 0.8) < 0.05 && cy < 0.5) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.3) + (cy - 0.5) * 0.8) < 0.05 && cy > 0.5) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else if (glyph_id == 47) { // | (pipe)
        return (abs(cx - 0.5) < 0.05) ? 1.0 : 0.0;
    }
    else if (glyph_id == 48) { // + (plus)
        float h = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.2 && cy < 0.8) ? 1.0 : 0.0;
        return h + v;
    }
    else if (glyph_id == 49) { // * (asterisk)
        float h = (abs(cy - 0.5) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.2 && cy < 0.8) ? 1.0 : 0.0;
        float d1 = (abs((cx - 0.5) - (cy - 0.5)) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) + (cy - 0.5)) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        return h + v + d1 + d2;
    }
    else if (glyph_id == 50) { // コ (Ko)
        float v1 = (abs(cx - 0.2) < 0.05) ? 1.0 : 0.0;
        float h1 = (abs(cy - 0.2) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        float h2 = (abs(cy - 0.8) < 0.05 && cx > 0.15 && cx < 0.8) ? 1.0 : 0.0;
        return v1 + h1 + h2;
    }
    else if (glyph_id == 51) { // シ (Shi)
        float d1 = (abs((cx - 0.2) - (cy - 0.3) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) - (cy - 0.3) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        float d3 = (abs((cx - 0.8) - (cy - 0.3) * 0.6) < 0.05 && cy > 0.2) ? 1.0 : 0.0;
        return d1 + d2 + d3;
    }
    else if (glyph_id == 52) { // マ (Ma)
        float v = (abs(cx - 0.5) < 0.05 && cy > 0.3) ? 1.0 : 0.0;
        float h = (abs(cy - 0.3) < 0.05 && cx > 0.2 && cx < 0.8) ? 1.0 : 0.0;
        float d1 = (abs((cx - 0.5) - (cy - 0.3) * 0.6) < 0.05 && cy < 0.3) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) + (cy - 0.3) * 0.6) < 0.05 && cy < 0.3) ? 1.0 : 0.0;
        return v + h + d1 + d2;
    }
    else if (glyph_id == 53) { // ム (Mu)
        float d1 = (abs((cx - 0.5) - (cy - 0.8) * 0.6) < 0.05) ? 1.0 : 0.0;
        float d2 = (abs((cx - 0.5) + (cy - 0.8) * 0.6) < 0.05) ? 1.0 : 0.0;
        return d1 + d2;
    }
    else { // メ (Me) - default for remaining indices
        float d1 = (abs((cx - cy)) < 0.05) ? 1.0 : 0.0;
        float d2 = (abs((cx + cy - 1.0)) < 0.05) ? 1.0 : 0.0;
        return d1 + d2;
    }
}

float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {
    if (!waveform_enabled || waveform_length <= 0) {
        return 0.0;
    }
    
    // Matrix parameters - optimized for glyph visibility
    float num_columns = 64 * waveform_scale;  // Balanced for performance and detail
    float char_height = 0.025;  // Slightly larger for better glyph visibility
    
    // Mirror the right side to the left for uniform appearance
    float mirrored_x = x_coord;
    if (x_coord < 0.5) {
        mirrored_x = 1.0 - x_coord;
    }
    
    // Determine which column we're in
    float column_index = floor(x_coord * num_columns);
    float column_x = fract(x_coord * num_columns);
    
    // Sample audio data for this column
    float audio_sample = texture(waveform_data, vec2(column_index / num_columns, 0.5)).r;
    float fft_sample = texture(fft_data, vec2(column_index / num_columns, 0.5)).r;
    
    // Create column-specific randomness
    float column_seed = tan(column_index * 12.9898) * 43758.5453;
    float column_random = fract(column_seed) ;
    
    // Column activity - audio-reactive
    float column_activity;
    column_activity = (300 * fft_sample + column_index);
    
    // Skip inactive columns for sparse effect
    if (column_activity < 0.15) {
        return 0.0;
    }
    
    // Rain speed varies per column and is audio-reactive
    float base_speed = 0.85 + (0.000001 / fft_sample) * 0.0009;
    float rain_time = time * base_speed + column_random * 42.0;
    float rain_y = fract(rain_time * 0.2);  // Slower falling for better glyph visibility
    
    float total_intensity = 0.0;
    
    // Main bright glyph at the front of the rain
    float main_char_y = 1.0 - rain_y;
    float char_row = floor(y_coord / char_height);
    float char_y_pos = fract(y_coord / char_height);
    
    // Character should be centered in column with proper width
    if (column_x > 0.15 && column_x < 0.85) {  // Better centering for glyphs
        float char_x_norm = (column_x - 0.15) / 0.7;  // Normalize to 0-1 within character
        
        // Main bright glyph (head of the stream)
        float main_char_row = floor(main_char_y / char_height);
        if (abs(char_row - main_char_row) < 0.5) {
            // Select glyph based on column and time for variety
            float glyph_selector = sin(column_index * 3.14159 + time * 1.2 + main_char_row * 0.5);
            float glyph_intensity = get_glyph_intensity(char_x_norm, char_y_pos, glyph_selector);
            
            if (glyph_intensity > 0.0) {
                // Bright head glyph with audio reactivity
                float brightness = 1.2 + fft_sample * 2.8;
                total_intensity += glyph_intensity * brightness;
            }
        }
        
        // Trailing glyphs - fewer and more spaced out
        for (int i = 1; i < 18; i++) {  // Reduced trail length for performance
            float trail_char_y = main_char_y + float(i) * char_height * 3.0;  // More spacing
            float trail_char_row = floor(trail_char_y / char_height);
            
            if (abs(char_row - trail_char_row) < 0.5 && trail_char_y < 1.0) {
                // Skip some trail characters randomly for sparse effect
                float trail_skip = sin(column_index * 0.9 + float(i) * 8.3 + time * 0.7);
                if (fract(trail_skip * 43758.5453) > 0.65) {  // 35% chance to show trail glyph
                    float trail_glyph_selector = sin(column_index * 7.234 + time * 0.15 + trail_char_row + float(i) * 1.7);
                    float trail_glyph_intensity = get_glyph_intensity(char_x_norm, char_y_pos, trail_glyph_selector);
                    
                    if (trail_glyph_intensity > 0.0) {
                        // Exponential fade for trail with audio modulation
                        float trail_brightness = pow(0.995, float(i)) * (0.65 + audio_sample * 0.35);
                        total_intensity += trail_glyph_intensity * trail_brightness;
                    }
                }
            }
        }
        
        // Subtle glyph flicker - less frequent
        float flicker_seed = sin(time * 3.5 + column_index + char_row) * 43758.5453;
        float flicker = fract(flicker_seed);
        if (flicker > 0.99) {  // Only 2% chance of flicker
            total_intensity *= 0.1;
        }
        

    }
    
    return clamp(total_intensity, 0.0, 1.0);
}

// Backward compatibility function
float compute_waveform_at_x(float x_coord) {
    return 0.0;
}
