package com.folotoy.meeting;

import android.app.Activity;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.widget.Button;
import android.widget.TextView;

/** FoloToy's current website palette. Status colors remain semantic. */
final class BrandUi {
    static final int PRIMARY=0xffdb2777, PRESSED=0xffc5236b, INK=0xff302331;
    static final int MUTED=0xff756674, BACK=0xfffdf8fa, LINE=0xffe8dbe2, SOFT=0xfffce7f0;
    private BrandUi() {}
    static int dp(Activity activity,int value) { return Math.round(activity.getResources().getDisplayMetrics().density*value); }
    static GradientDrawable surface(Activity activity,int color,int radius) {
        GradientDrawable shape=new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(activity,radius)); return shape;
    }
    static void button(Activity activity,Button button,boolean primary) {
        button.setAllCaps(false); button.setTextSize(16); button.setMinHeight(dp(activity,52));
        button.setPadding(dp(activity,14),dp(activity,12),dp(activity,14),dp(activity,12));
        button.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{}},
            new int[]{MUTED,primary?Color.WHITE:PRESSED}));
        GradientDrawable shape=surface(activity,Color.WHITE,14);
        shape.setColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled},new int[]{android.R.attr.state_pressed},new int[]{}},
            new int[]{LINE,primary?PRESSED:SOFT,primary?PRIMARY:Color.WHITE}));
        if(!primary) shape.setStroke(dp(activity,1),LINE);
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(primary?0x11ffffff:0x22db2777),shape,null));
    }
    static TextView back(Activity activity,String parent,Runnable action) {
        TextView view=new TextView(activity); view.setText("‹  "+parent); view.setContentDescription(parent);
        view.setTextColor(PRESSED); view.setTextSize(16); view.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        view.setMinHeight(dp(activity,48)); view.setGravity(Gravity.CENTER_VERTICAL);
        view.setPadding(dp(activity,4),0,dp(activity,8),0);
        view.setBackground(new RippleDrawable(ColorStateList.valueOf(SOFT),null,surface(activity,Color.WHITE,10)));
        view.setOnClickListener(v->action.run()); return view;
    }
}
