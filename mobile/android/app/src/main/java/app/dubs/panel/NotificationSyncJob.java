package app.dubs.panel;
import android.app.job.JobParameters;
import android.app.job.JobService;
/**
 * Periodic local-notification poll. Exported=false and restricted to
 * BIND_JOB_SERVICE: nothing external can reach it, no arbitrary extras are
 * forwarded (onStartJob ignores parameters entirely).
 */
public final class NotificationSyncJob extends JobService {
 private Thread worker;
 @Override public boolean onStartJob(JobParameters params){
  worker=new Thread(()->{
   boolean retry=false;
   try{retry=NotificationSync.pollOnce(this);}
   catch(Exception failure){android.util.Log.w("DubsEvents",failure.getClass().getSimpleName());}
   finally{jobFinished(params,retry);}
  },"dubs-events");worker.start();
  return true;
 }
 @Override public boolean onStopJob(JobParameters params){if(worker!=null)worker.interrupt();return false;}
}
