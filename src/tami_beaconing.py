#! /usr/bin/python

# Import the core Python modules for ROS and to implement ROS Actions:
import rospy
import math
# Import some image processing modules:
import cv2
from cv_bridge import CvBridge
# Import all the necessary ROS message types:
from sensor_msgs.msg import Image
from sensor_msgs.msg import LaserScan
# Import some other modules from within this package
from move_tb3 import MoveTB3
from tb3_odometry import TB3Odometry
from obstacle_avoidance import *

# Import other modules
import numpy as np
import math

# Rotating the robot for looking around leads to unintended changes in direction
# Just mae the view wider

class Beaconing(object):
    def __init__(self, speed=0.2, look_around_time=6):
        self.speed = speed
        self.state = 0 # 0 = looking for beacon, 1 = Found Beacon

        rospy.init_node('beaconing')

        # camera + colour
        self.view_high = False
        self.cam = rospy.Subscriber('/camera/rgb/image_raw', Image, self.camera_callback)
        self.hsv_img = np.zeros((1920, 1080, 3), np.uint8)
        self.target_colour_bounds = ([0, 0, 100], [255, 255, 255])
        self.mask = np.zeros((1920, 1080, 1), np.uint8)
        self.colour_boundaries = {
            "Red":    ([0, 185, 100], [10, 255, 255]),
            "Blue":   ([115, 224, 100],   [130, 255, 255]),
            "Yellow": ([28, 180, 100], [32, 255, 255]),
            "Green":   ([25, 150, 100], [70, 255, 255]),
            "Turquoise":   ([75, 50, 100], [90, 255, 255]),
            "Purple":   ([145, 185, 100], [150, 250, 255])
        }
        self.m00 = 0
        self.m00_min = 10000

        self.width_crop = 800

        self.robot = MoveTB3()
        self.robot_odom = TB3Odometry()
        self.oa = ObstacleAvoidance(fdist_thresh=0.55, left_range=70, right_range=70, robot_controller=self.robot, init=False)


        self.cvbridge_interface = CvBridge()
        
        self.ctrl_c = False
        rospy.on_shutdown(self.shutdown)

        r = 20 # Hz
        self.rate = rospy.Rate(r)
        self.look_around_time = r * look_around_time # look around every 3 seconds
        self.look_around_countdown = r * 10 # So that we don't start looking around immediately

        self.target_check = False
        self.found_target = False
        self.task_complete = False
        self.park_stage = 0

    def shutdown(self):
        cv2.destroyAllWindows()
        self.ctrl_c = True
    
    def camera_callback(self, img_data):
        try:
            cv_img = self.cvbridge_interface.imgmsg_to_cv2(img_data, desired_encoding="bgr8")
        except CvBridgeError as e:
            print(e)
        
        height, width, channels = cv_img.shape

        crop_width = width - 800
        crop_height = 300
        offset = 300 if self.view_high else 0

        crop_x = int((width/2) - (crop_width/2))
        crop_y = int((height - crop_height - offset) - (crop_height/2))

        crop_img = cv_img[crop_y:crop_y+crop_height, 0:width]

        self.hsv_img = cv2.cvtColor(crop_img, cv2.COLOR_BGR2HSV)

        self.mask = cv2.inRange(self.hsv_img, np.array(self.target_colour_bounds[0]), np.array(self.target_colour_bounds[1]))

        m = cv2.moments(self.mask)
        self.m00 = m['m00']
        self.cy = m['m10'] / (m['m00'] + 1e-5)
        self.width = width

        if self.m00 > self.m00_min:
            cv2.circle(crop_img, (int(self.cy), crop_height / 2), 10, (0, 0, 255), 2)
        
        cv2.imshow('cropped image', crop_img)
        cv2.waitKey(1)

    def colour_selection(self):
        for name, (lower, upper) in self.colour_boundaries.items():
            lb = np.array(lower)
            ub = np.array(upper)
            mask_checker = cv2.inRange(self.hsv_img, lb, ub)

            if mask_checker.any():
                return name, (lower, upper)

    def check_for_target(self):
        selection = self.colour_selection()
        if selection != None:
            _, bounds = selection
            self.found_target = bounds == self.target_colour_bounds
            return True
        return False
    
    def look_around(self):
        print("Look Around")
        angle = 30 # Sweep 30 Deg

        # Check forward
        found_a_col = self.check_for_target()
        if self.found_target:
            return
        # Check left
        self.robot.deg_rotate(-angle)
        found_a_col |= self.check_for_target()
        if self.found_target:
            return
        
        self.robot.deg_rotate(angle)

        # Check right
        self.robot.deg_rotate(angle)
        found_a_col |= self.check_for_target()
        if self.found_target:
            return
        
        self.robot.deg_rotate(-angle)

        if found_a_col:
            self.robot.deg_rotate(-10)
        

    def seek(self):
        self.oa.attempt_avoidance()
        self.robot.set_move_cmd(self.speed, 0.0)
        self.robot.publish()

        if self.target_check:
            self.check_for_target()
        
        self.look_around_countdown -= 1
        if self.look_around_countdown < 1:
            # self.width_crop = 100
            self.target_check = True
            # self.look_around_countdown = self.look_around_time
            # self.look_around()

    def park_at_target(self):
        if self.park_stage == 0:
            print("Found Target Colour")
            self.view_high = True
            self.found_target = False
            self.park_stage += 1
            self.robot.set_move_cmd(0.0, 0.0)
            self.robot.publish()
        elif self.park_stage == 1:
            self.check_for_target()
            if self.m00 > self.m00_min:
                self.park_stage += 1
                print("Lock on {}".format(self.cy))
            else:
                self.robot.deg_rotate(5)
        elif self.park_stage == 2:
            rot = ((self.cy / self.width) - (0.5 * (0 if self.cy == 0.0 else 1))) * -1
            self.robot.deg_rotate(rot)

            straight_ahead = abs(rot) < 0.1
            # print("{} {}".format(rot, straight_ahead))
            if straight_ahead:
                self.park_stage += 1
                self.robot_odom.cache_current_data()
            # self.robot.set_move_cmd(0.1, 0.0)
            # self.robot.publish()
        else:
            self.robot.set_move_cmd(self.speed, 0.0)
            self.robot.publish()
            # Need to integrate some odom in here so we steer out of the
            # the way of things and still head to the goal

            # at the start of this step cache the yaw
            # when the yaw deviates from that value, wait a bit
            # then take into account the distance covered in that distance
            # then attempt to rotate to make up for that distance 

            if self.m00 == 0.0:
                self.park_stage = 0
                self.found_target = False

            self.oa.attempt_avoidance()

            if (self.robot_odom.yaw != self.robot_odom.cache_yaw):
                print("Curr Y: {} Cached Y: {} M00: {}".format(self.robot_odom.yaw, self.robot_odom.cache_yaw, self.m00))

            # Good M00: 101439000.0, 142878795.0, 94630500.0

            if self.oa.lidar[FRONT] < 0.3:
                print("M00: {}".format(self.m00))
                self.task_complete = True

    
    def begin(self):
        rospy.sleep(1)
        self.robot.deg_rotate(90)

        target, self.target_colour_bounds = self.colour_selection()

        self.robot.deg_rotate(-90)

        self.robot.set_move_cmd(self.speed, 0.0)
        self.robot.publish()

        print("SEARCH INITITATED: The target colour is {}".format(target))

        p = True

        while not (self.ctrl_c or self.task_complete):
            if self.found_target or self.view_high:
                self.park_at_target()
            else:
                self.seek()

            # p = not p
            # if p:
            #     print("X: {}, Y: {}".format(self.robot_odom.posx, self.robot_odom.posy))

            self.rate.sleep()

        self.robot.stop()

        if self.task_complete:
            print("BEACONING COMPLETE: The robot has now reached the target")
         
if __name__ == '__main__':
    b = Beaconing()
    try:
        b.begin()
    except rospy.ROSInterruptException:
        pass
