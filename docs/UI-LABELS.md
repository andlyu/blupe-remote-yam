# Robot status meanings

- **Ready to queue:** You can submit a prompt. The robot may still need to become ready before it starts.
- **Ready for the next run:** The robot reports that it is ready to accept work.
- **Queued — waiting for your turn:** Your prompt is accepted and waiting to be assigned.
- **Queued — waiting for robot readiness:** Your prompt is accepted, but the robot has not reported that it is ready.
- **Preparing:** The robot is getting ready for your task.
- **Running:** Your task is active. This includes time spent waiting for the model to decide what to do.
- **Waiting for robot control:** Your run is active, but robot control is not available yet.
- **Stopped — waiting for operator readiness:** The operator needs to ready the robot.
- **Offline:** The robot is disconnected.
- **Fault:** The robot has reported a problem that needs attention.
- **Checking / unavailable:** The UI does not have the robot’s current status.
- **Robot <mode>:** A fallback showing another controller state, such as homing, parking, or holding.
