import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { clearLapsed, openCommand, useLapsed } from "@/lib/session";

/**
 * The session lapsed (/api/web answered 401): the page stays as it is, with whatever was typed in it, and this says
 * how to sign in again. Retry asks the reads again; a 401 brings the dialog straight back.
 */
export function SessionDialog() {
  const lapsed = useLapsed();
  const location = useLocation();
  const client = useQueryClient();
  const retry = () => {
    clearLapsed();
    void client.refetchQueries({ type: "active" });
  };
  return (
    <AlertDialog open={lapsed}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Your session has ended</AlertDialogTitle>
          <AlertDialogDescription>
            Sign in again from a terminal on this machine, then press Retry. Nothing on this page is lost.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <code className="block rounded-sm border border-rule bg-paper px-3 py-2 font-mono text-sm break-all select-all">
          {openCommand(location)}
        </code>
        <AlertDialogFooter>
          <Button variant="default" onClick={retry}>
            Retry
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
