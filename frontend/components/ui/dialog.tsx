"use client";

import * as React from "react";
import { Dialog as BaseDialog } from "@base-ui/react/dialog";

import { cn } from "@/lib/utils";

const Dialog = BaseDialog.Root;
const DialogClose = BaseDialog.Close;

const DialogContent = React.forwardRef<
  React.ElementRef<typeof BaseDialog.Popup>,
  React.ComponentPropsWithoutRef<typeof BaseDialog.Popup>
>(({ className, children, ...props }, ref) => (
  <BaseDialog.Portal>
    <BaseDialog.Backdrop className="fixed inset-0 z-50 bg-ink/40 backdrop-blur-sm" />
    <BaseDialog.Popup
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl bg-paper-raised shadow-2xl focus:outline-none",
        className
      )}
      {...props}
    >
      {children}
    </BaseDialog.Popup>
  </BaseDialog.Portal>
));
DialogContent.displayName = "DialogContent";

export { Dialog, DialogClose, DialogContent };
