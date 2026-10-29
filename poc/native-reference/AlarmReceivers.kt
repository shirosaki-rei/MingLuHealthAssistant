package com.minglu.alarm

// 明路精确闹钟 —— Kotlin 参考实现（不进构建，供 UTS 桥接时对照）
//
// 用途：D6 写 UTS 插件时，把这两个 Receiver 放到 app-android 原生源码目录，
// 由 UTS 侧用 setClassName 触发。若 UTS 的类引用方式卡住，可改用反射或原生插件方案。
//
// 必须在 AndroidManifest 中声明：
//   <receiver android:name=".AlarmReceiver" android:exported="false"/>
//   <receiver android:name=".BootReceiver" android:exported="true">
//     <intent-filter>
//       <action android:name="android.intent.action.BOOT_COMPLETED"/>
//     </intent-filter>
//   </receiver>
// 权限：SCHEDULE_EXACT_ALARM / POST_NOTIFICATIONS / RECEIVE_BOOT_COMPLETED / WAKE_LOCK

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

const val ACTION_FIRE = "com.minglu.alarm.FIRE"
const val PREF = "minglu_alarm"
const val CHANNEL_ID = "minglu_reminder"

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getIntExtra("id", 0)
        val title = intent.getStringExtra("title") ?: "明路提醒"
        val content = intent.getStringExtra("content") ?: ""
        showNotification(context, id, title, content)
        // 触发后清掉记录，避免开机时重复注册
        context.getSharedPreferences(PREF, Context.MODE_PRIVATE)
            .edit().remove("alarm_$id").apply()
    }

    private fun showNotification(context: Context, id: Int, title: String, content: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, "健康提醒", NotificationManager.IMPORTANCE_HIGH)
                )
            }
            Notification.Builder(context, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context)
        }

        builder.setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(content)
            .setAutoCancel(true)
            .setWhen(System.currentTimeMillis())

        nm.notify(id, builder.build())
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val sp = context.getSharedPreferences(PREF, Context.MODE_PRIVATE)
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val now = System.currentTimeMillis()

        // 遍历所有未触发的闹钟，重新注册
        for ((key, value) in sp.all) {
            if (!key.startsWith("alarm_")) continue
            val id = key.removePrefix("alarm_").toIntOrNull() ?: continue
            val at = (value as? Long) ?: continue

            if (at <= now) {
                sp.edit().remove(key).apply()
                continue
            }

            val fire = Intent(context, AlarmReceiver::class.java).apply {
                action = ACTION_FIRE
                putExtra("id", id)
                putExtra("title", "明路提醒")
                putExtra("content", "重启后已恢复的提醒")
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            val pi = PendingIntent.getBroadcast(context, id, fire, flags)
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        }
    }
}
