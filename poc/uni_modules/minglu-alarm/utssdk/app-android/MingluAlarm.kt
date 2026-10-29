package com.minglu.alarm

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// 明路精确闹钟 —— Android 原生实现
// 放在 utssdk/app-android/ 下会被 UTS 插件编译链自动纳入构建

const val ML_ACTION_FIRE = "com.minglu.alarm.FIRE"
const val ML_PREF = "minglu_alarm"
const val ML_CHANNEL_ID = "minglu_reminder"
const val ML_TAG = "MingluAlarm"

object MingluAlarm {

    /** 当前设备是否允许精确闹钟（Android 12+ 需要用户授权） */
    fun canScheduleExact(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            return am.canScheduleExactAlarms()
        }
        return true
    }

    /** 跳到系统的「闹钟与提醒」授权页 */
    fun openExactAlarmSettings(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            try {
                val intent = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
                intent.data = Uri.parse("package:" + context.packageName)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
            } catch (e: Exception) {
                // 部分 ROM 没有这个页面，退回到应用详情页
                val fallback = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                fallback.data = Uri.parse("package:" + context.packageName)
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(fallback)
            }
        }
    }

    /** 设定一次性精确闹钟，返回是否成功
     *  id / triggerAtMs 用 Number 接收：UTS 的 number 编译到 Kotlin 就是 Number，
     *  写成 Double 会报 "actual type is Number, but Double was expected" */
    fun setExact(
        context: Context,
        id: Number,
        triggerAtMs: Number,
        title: String,
        content: String
    ): Boolean {
        if (!canScheduleExact(context)) return false
        ensureReceiver(context)
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val at = triggerAtMs.toLong()
        val reqId = id.toInt()
        try {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, buildPendingIntent(context, reqId, title, content))
        } catch (e: SecurityException) {
            return false
        }
        // 记录，供开机后恢复
        context.getSharedPreferences(ML_PREF, Context.MODE_PRIVATE)
            .edit().putLong("alarm_" + reqId, at).apply()
        Log.d(ML_TAG, "setExact ok id=" + reqId + " at=" + at + " now=" + System.currentTimeMillis())
        return true
    }

    /**
     * 立即手动触发一次广播，不走 AlarmManager。
     * 用途：判断 Receiver 有没有被注册进 APK。
     *   - 有通知 + 最近触发时间更新 → Receiver 正常，问题在闹钟调度
     *   - 毫无反应 → Receiver 没注册（manifest 没合并）
     */
    fun fireNow(context: Context, id: Number, title: String, content: String): Boolean {
        ensureReceiver(context)
        // 用隐式 Intent（只指定 action + package，不指定 component）：
        // 显式广播只会投递给静态注册的组件，匹配不到动态注册的 Receiver。
        val intent = Intent(ML_ACTION_FIRE)
        intent.setPackage(context.packageName)
        intent.putExtra("id", id.toInt())
        intent.putExtra("title", title)
        intent.putExtra("content", content)
        context.sendBroadcast(intent)
        Log.d(ML_TAG, "fireNow sent id=" + id)
        return true
    }

    /** 取消指定 id 的闹钟 */
    fun cancel(context: Context, id: Number) {
        val reqId = id.toInt()
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(buildPendingIntent(context, reqId, "", ""))
        context.getSharedPreferences(ML_PREF, Context.MODE_PRIVATE)
            .edit().remove("alarm_" + reqId).apply()
    }

    /** 通知权限是否开启（Android 13+ 需要运行时授权，只声明清单不够） */
    fun areNotificationsEnabled(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            return nm.areNotificationsEnabled()
        }
        return true
    }

    /** 跳到本应用的通知设置页，让用户手动开通知 */
    fun openNotificationSettings(context: Context) {
        try {
            val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                    putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                }
            } else {
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:" + context.packageName)
                }
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
        } catch (e: Exception) {
            // 部分 ROM 没有标准页面，忽略
        }
    }

    /** 最近一次闹钟触发时间，空串表示从未触发过
     *  返回值用 String，避免 Long/Number 在 UTS 侧的类型转换问题 */
    fun lastFireAt(context: Context): String {
        val t = context.getSharedPreferences(ML_PREF, Context.MODE_PRIVATE)
            .getLong("last_fire_at", 0L)
        if (t == 0L) return ""
        val fmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
        return fmt.format(Date(t))
    }

    /**
     * 查询 Receiver 是否已声明在 APK 里。
     * 不能用 queryBroadcastReceivers —— 那是按 action 查的，
     * 而我们的 Receiver 没有 intent-filter，查不到。
     * 正确做法：getPackageInfo + GET_RECEIVERS，列出所有声明过的 receiver。
     */
    fun isReceiverRegistered(context: Context): Boolean {
        return try {
            val info = context.packageManager.getPackageInfo(
                context.packageName,
                PackageManager.GET_RECEIVERS
            )
            var found = false
            val receivers = info.receivers
            if (receivers != null) {
                for (r in receivers) {
                    if (r.name == "com.minglu.alarm.MingluAlarmReceiver") {
                        found = true
                        break
                    }
                }
            }
            Log.d(ML_TAG, "isReceiverRegistered -> " + found)
            found
        } catch (e: Exception) {
            Log.e(ML_TAG, "query receiver failed: " + e.message)
            false
        }
    }

    private var dynReceiver: MingluAlarmReceiver? = null

    /**
     * 兜底：若静态注册没生效，就在进程内动态注册一份。
     * 限制：App 进程被杀后失效，所以它只是让 PoC 能跑通，
     * 正式版仍必须解决静态注册。
     */
    fun ensureReceiver(context: Context) {
        if (isReceiverRegistered(context)) {
            Log.d(ML_TAG, "static receiver present, skip dynamic")
            return
        }
        if (dynReceiver != null) return
        try {
            val receiver = MingluAlarmReceiver()
            val filter = IntentFilter(ML_ACTION_FIRE)
            if (Build.VERSION.SDK_INT >= 33) {
                context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                context.registerReceiver(receiver, filter)
            }
            dynReceiver = receiver
            Log.d(ML_TAG, "dynamic receiver registered")
        } catch (e: Exception) {
            Log.e(ML_TAG, "registerReceiver failed: " + e.message)
        }
    }

    /** 内部用，也供 BootReceiver 复用 */
    fun buildPendingIntent(context: Context, id: Int, title: String, content: String): PendingIntent {
        // 闹钟用显式 Intent：静态注册的 Receiver 一定能命中，
        // 不受 intent-filter 是否配置影响，也不受隐式广播后台限制影响。
        // （fireNow 走隐式，那是为了兼容标准基座的动态注册，两者职责不同）
        val intent = Intent(context, MingluAlarmReceiver::class.java)
        intent.action = ML_ACTION_FIRE
        intent.putExtra("id", id)
        intent.putExtra("title", title)
        intent.putExtra("content", content)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        return PendingIntent.getBroadcast(context, id, intent, flags)
    }
}

/** 闹钟触发：弹本地通知 */
class MingluAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getIntExtra("id", 0)
        val title = intent.getStringExtra("title") ?: "明路提醒"
        val content = intent.getStringExtra("content") ?: ""
        Log.d(ML_TAG, "onReceive fired id=" + id + " action=" + intent.action)
        // 先记录触发时间：用来区分「闹钟根本没触发」和「触发了但通知被系统拦掉」
        context.getSharedPreferences(ML_PREF, Context.MODE_PRIVATE)
            .edit()
            .putLong("last_fire_at", System.currentTimeMillis())
            .remove("alarm_" + id)
            .apply()
        showNotification(context, id, title, content)
    }

    @Suppress("DEPRECATION")
    private fun showNotification(context: Context, id: Int, title: String, content: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val builder: Notification.Builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            if (nm.getNotificationChannel(ML_CHANNEL_ID) == null) {
                val ch = NotificationChannel(
                    ML_CHANNEL_ID,
                    "健康提醒",
                    NotificationManager.IMPORTANCE_HIGH
                )
                nm.createNotificationChannel(ch)
            }
            Notification.Builder(context, ML_CHANNEL_ID)
        } else {
            Notification.Builder(context)
        }

        builder.setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(content)
            .setAutoCancel(true)
            .setWhen(System.currentTimeMillis())

        try {
            nm.notify(id, builder.build())
        } catch (e: SecurityException) {
            // 未授予通知权限时忽略，不影响闹钟本身触发
        }
    }
}

/** 开机后把还没触发的闹钟重新注册一遍 */
class MingluBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val sp = context.getSharedPreferences(ML_PREF, Context.MODE_PRIVATE)
        val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val now = System.currentTimeMillis()

        for ((key, value) in sp.all) {
            if (!key.startsWith("alarm_")) continue
            val id = key.removePrefix("alarm_").toIntOrNull() ?: continue
            val at = (value as? Long) ?: continue

            if (at <= now) {
                sp.edit().remove(key).apply()
                continue
            }

            try {
                val pi = MingluAlarm.buildPendingIntent(context, id, "明路提醒", "重启后已恢复的提醒")
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            } catch (e: Exception) {
                // 单个恢复失败不影响其他
            }
        }
    }
}
