import { useState, useEffect, useCallback, useRef } from "react";

import { toast } from "sonner";

import { useLocalSettings } from "../settings";

interface NotificationOptions {
  body?: string;
  icon?: string;
  badge?: string;
  tag?: string;
  data?: unknown;
  requireInteraction?: boolean;
  silent?: boolean;
}

interface UseNotificationReturn {
  permission: NotificationPermission;
  isSupported: boolean;
  requestPermission: () => Promise<NotificationPermission>;
  showNotification: (title: string, options?: NotificationOptions) => void;
}

export function useNotification(): UseNotificationReturn {
  const [permission, setPermission] =
    useState<NotificationPermission>("default");
  const [isSupported, setIsSupported] = useState(false);

  const lastNotificationTime = useRef<Date | null>(null);

  useEffect(() => {
    if ("Notification" in window) {
      setIsSupported(true);
      setPermission(Notification.permission);
    }
  }, []);

  const requestPermission =
    useCallback(async (): Promise<NotificationPermission> => {
      if (!isSupported) {
        console.warn("Notification API is not supported in this browser");
        toast.error("当前浏览器不支持通知功能");
        return "denied";
      }

      const result = await Notification.requestPermission();
      setPermission(result);
      if (result === "granted") {
        toast.success("通知权限已开启");
      } else if (result === "denied") {
        toast.error("通知权限被拒绝，请在浏览器设置中手动开启");
      }
      return result;
    }, [isSupported]);

  const [settings] = useLocalSettings();

  const showNotification = useCallback(
    (title: string, options?: NotificationOptions) => {
      if (!isSupported) {
        console.warn("Notification API is not supported");
        toast.error("当前浏览器不支持通知功能");
        return;
      }

      if (permission !== "granted") {
        console.warn("Notification permission not granted");
        toast.error("未获得通知权限，请先开启通知权限");
        return;
      }

      if (!settings.notification.enabled) {
        console.warn("Notification is disabled");
        toast.error("通知功能未启用，请先在设置中开启");
        return;
      }

      if (
        lastNotificationTime.current &&
        new Date().getTime() - lastNotificationTime.current.getTime() < 1000
      ) {
        console.warn("Notification sent too soon");
        toast.warning("发送过于频繁，请稍后再试");
        return;
      }
      lastNotificationTime.current = new Date();

      try {
        const notification = new Notification(title, options);
        toast.success("测试通知已发送");

        notification.onclick = () => {
          window.focus();
          notification.close();
        };

        notification.onerror = (error) => {
          console.error("Notification error:", error);
          toast.error("通知显示失败，请检查系统通知设置");
        };
      } catch (error) {
        console.error("Failed to create notification:", error);
        toast.error("通知发送失败，请检查浏览器通知设置");
      }
    },
    [isSupported, settings.notification.enabled, permission],
  );

  return {
    permission,
    isSupported,
    requestPermission,
    showNotification,
  };
}
