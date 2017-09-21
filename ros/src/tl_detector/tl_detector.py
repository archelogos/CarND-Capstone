#!/usr/bin/env python
import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped, Pose
from styx_msgs.msg import TrafficLightArray, TrafficLight
from styx_msgs.msg import Lane
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from light_classification.tl_classifier import TLClassifier
import tf
import tf.transformations as tft
import cv2
import yaml
import sys
import math
import numpy
#from pyquaternion import Quaternion

STATE_COUNT_THRESHOLD = 3

class TLDetector(object):
    def __init__(self):
        rospy.init_node('tl_detector')

        self.pose = None
        self.waypoints = None
        self.camera_image = None
        self.has_image = False
        self.lights = []

        sub1 = rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        sub2 = rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        '''
        /vehicle/traffic_lights provides you with the location of the traffic light in 3D map space and
        helps you acquire an accurate ground truth data source for the traffic light
        classifier by sending the current color state of all traffic lights in the
        simulator. When testing on the vehicle, the color state will not be available. You'll need to
        rely on the position of the light and the camera image to predict it.
        '''
        sub3 = rospy.Subscriber('/vehicle/traffic_lights', TrafficLightArray, self.traffic_cb)
        sub6 = rospy.Subscriber('/image_color', Image, self.image_cb)

        config_string = rospy.get_param("/traffic_light_config")
        self.config = yaml.load(config_string)

        self.upcoming_red_light_pub = rospy.Publisher('/traffic_waypoint', Int32, queue_size=1)

        self.bridge = CvBridge()
        self.light_classifier = TLClassifier()
        self.listener = tf.TransformListener()

        self.state = TrafficLight.UNKNOWN
        self.last_state = TrafficLight.UNKNOWN
        self.last_wp = -1
        self.state_count = 0

        #model = load_model('light_classifier_model.h5')
        #model.summary()

        rospy.spin()

    def pose_cb(self, msg):
        self.pose = msg

    def waypoints_cb(self, waypoints):
        self.waypoints = waypoints

    def traffic_cb(self, msg):
        self.lights = msg.lights

    def image_cb(self, msg):
        """Identifies red lights in the incoming camera image and publishes the index
            of the waypoint closest to the red light's stop line to /traffic_waypoint

        Args:
            msg (Image): image from car-mounted camera

        """
        
        #print('image_cb')
        if not self.has_image:
            rospy.loginfo("Received image from simulator")
        self.has_image = True
        self.camera_image = msg
        light_wp, state = self.process_traffic_lights()

        '''
        Publish upcoming red lights at camera frequency.
        Each predicted state has to occur `STATE_COUNT_THRESHOLD` number
        of times till we start using it. Otherwise the previous stable state is
        used.
        '''
        if self.state != state:
            self.state_count = 0
            self.state = state
        elif self.state_count >= STATE_COUNT_THRESHOLD:
            self.last_state = self.state
            light_wp = light_wp if state == TrafficLight.RED else -1
            self.last_wp = light_wp
            self.upcoming_red_light_pub.publish(Int32(light_wp))
        else:
            #i = 1
            self.upcoming_red_light_pub.publish(Int32(self.last_wp))
        self.state_count += 1

    def get_closest_index(self, pose, pose_list):
        """Identifies the closest lights to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to

        Returns:
            int: index of the closest waypoint in self.waypoints

        """
        #TODO implement
        #TODO(denise) implement optimized algorithm
        min_dist = 1e100
        index = 0
        for i, pt in enumerate(pose_list):
            dist = math.hypot(pt.pose.pose.position.x-pose.position.x, pt.pose.pose.position.y-pose.position.y)
            if dist < min_dist:
                min_dist = dist
                index = i
        return index


    def project_to_image_plane(self, point_in_world):
        """Project point from 3D world coordinates to 2D camera image location

        Args:
            point_in_world (Point): 3D location of a point in the world

        Returns:
            x (int): x coordinate of target point in image
            y (int): y coordinate of target point in image

        """

        fx = self.config['camera_info']['focal_length_x']
        fy = self.config['camera_info']['focal_length_y']
        # just override the simulator fx and fy
        if fx < 10:
            fx = 2244
        if fy < 10: 
            fy = 2552 #303.8
        image_width = self.config['camera_info']['image_width']
        image_height = self.config['camera_info']['image_height']

        # get transform between pose of camera and world frame
        transT = None
        rotT = None
        try:
            now = rospy.Time.now()
            self.listener.waitForTransform("/base_link",
                  "/world", now, rospy.Duration(1.0))
            (transT, rotT) = self.listener.lookupTransform("/base_link",
                  "/world", now)

        except (tf.Exception, tf.LookupException, tf.ConnectivityException):
            rospy.logerr("Failed to find camera to map transform")
            return None, None

        rpy = tf.transformations.euler_from_quaternion(rotT)
        yaw = rpy[2]

        (ptx, pty, ptz) = (point_in_world.x, point_in_world.y, point_in_world.z)

        point_to_cam = (ptx * math.cos(yaw) - pty * math.sin(yaw),
                        ptx * math.sin(yaw) + pty * math.cos(yaw), 
                        ptz)
        point_to_cam = [sum(x) for x in zip(point_to_cam, transT)]

        # DELETE THIS MAYBE
        point_to_cam[2] -= 1.0

        #rospy.loginfo_throttle(3, "traffic light location: " + str(ptx) + "," + str(pty) + "," + str(ptz))
        #rospy.loginfo_throttle(3, "cam to world trans: " + str(transT))
        #rospy.loginfo_throttle(3, "cam to world rot: " + str(rotT))
        #rospy.loginfo_throttle(3, "roll, pitch, yaw: " + str(rpy))
        rospy.loginfo_throttle(3, "traffic light to cam: " + str(point_to_cam))

        x = -point_to_cam[1] * fx / point_to_cam[0]; 
        y = -point_to_cam[2] * fy / point_to_cam[0]; 

        x = int(x + image_width/2)
        #y = int(y + image_height/2) 

        # DELETE THIS MAYBE
        y = int(y + image_height) 

        rospy.loginfo_throttle(3, "traffic light pixel (x,y): " + str(x) + "," + str(y))

        # x = 0
        # y = 0

        return (x, y)

    def get_light_state(self, light):
        """Determines the current color of the traffic light

        Args:
            light (TrafficLight): light to classify

        Returns:
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        ###TODO(denise) Replace with CV later
        #print('get light state')
        if(not self.has_image):
            self.prev_light_loc = None
            return False

        self.camera_image.encoding = "rgb8"
        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")



        x, y = self.project_to_image_plane(light.pose.pose.position)

        #pil_image = Image.fromstring("L", cv.GetSize(cv_image), cv_image.tostring())
        #crop image

        #img_array = img_to_array(pil_image.resize((64, 64), Image.ANTIALIAS))
        #img_array = img_array/255
        #img_array =  img_array[None, :]
        #model.predict(img_array)





        #TODO (denise) what should this size be?
        #stoplights are about 1067x356 mm
        #cv2.rectangle(cv_image,(x,y),(x-10,y+10),(0,255,0),3)
        #image for debugging
        #cv2.imwrite('test.png',cv_image)

        #TODO use light location to zoom in on traffic light in image

        #Get classification
        return self.light_classifier.get_classification(cv_image)

    def process_traffic_lights(self):
        """Finds closest visible traffic light, if one exists, and determines its
            location and color

        Returns:
            int: index of waypoint closes to the upcoming stop line for a traffic light (-1 if none exists)
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """

        light = None

        # List of positions that correspond to the line to stop in front of for a given intersection
        stop_line_positions = self.config['stop_line_positions']
        
        if(self.pose and self.waypoints):
            #TODO find the closest visible traffic light (if one exists)
            light_position = self.get_closest_index(self.pose.pose, self.lights)
            state = self.lights[light_position].state
            light_wp = self.get_closest_index(self.lights[light_position].pose.pose, self.waypoints.waypoints)
            #TODO (denise) this should be the final state used
            calc_state = self.get_light_state(self.lights[light_position])
            return light_wp, state


        if light:
            state = self.get_light_state(light)
            return light_wp, state
        return -1, TrafficLight.UNKNOWN

if __name__ == '__main__':
    try:
        TLDetector()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start traffic node.')
