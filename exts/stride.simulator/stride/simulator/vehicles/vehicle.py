# Numerical computations
import numpy as np
from scipy.spatial.transform import Rotation

# Low level APIs
import carb
from pxr import Usd, Gf

# High level Isaac sim APIs
import omni.usd
from omni.isaac.core.utils.prims import define_prim, get_prim_at_path
from omni.usd import get_stage_next_free_path
from omni.isaac.core.robots.robot import Robot

# Extension APIs
from stride.simulator.vehicles.state import State
from stride.simulator.interfaces.stride_sim_interface import StrideInterface
from stride.simulator.vehicles.vehicle_manager import VehicleManager


def get_world_transform_xform(prim: Usd.Prim):
    """
    Get the local transformation of a prim using omni.usd.get_world_transform_matrix().
    See https://docs.omniverse.nvidia.com/kit/docs/omni.usd/latest/omni.usd/omni.usd.get_world_transform_matrix.html
    Args:
        prim (Usd.Prim): The prim to calculate the world transformation.
    Returns:
        A tuple of:
        - Translation vector.
        - Rotation quaternion, i.e. 3d vector plus angle.
        - Scale vector.
    """
    world_transform: Gf.Matrix4d = omni.usd.get_world_transform_matrix(prim)
    rotation: Gf.Rotation = world_transform.ExtractRotation()
    return rotation


class Vehicle(Robot):
    """
    Default vehicle class
    """

    def __init__(  # pylint: disable=dangerous-default-value FIXME
            self,
            stage_prefix: str,
            usd_path: str = None,
            init_pos=[0.0, 0.0, 0.0],
            init_orientation=[0.0, 0.0, 0.0, 1.0],
    ):
        """
        Class that initializes a vehicle in the isaac sim's curent stage

        Args:
            stage_prefix (str): The name the vehicle will present in the simulator when spawned.
                                Defaults to "quadrupedrobot".
            usd_path (str): The USD file that describes the looks and shape of the vehicle. Defaults to "".
            init_pos (list): The initial position of the vehicle in the inertial frame (in ENU convention).
                                Defaults to [0.0, 0.0, 0.0].
            init_orientation (list): The initial orientation of the vehicle in quaternion [qx, qy, qz, qw].
                                    Defaults to [0.0, 0.0, 0.0, 1.0].
        """

        # Get the current world at which we want to spawn the vehicle
        self._world = StrideInterface().world
        self._current_stage = self._world.stage

        # Save the name with which the vehicle will appear in the stage
        # and the name of the .usd file that contains its description
        self._stage_prefix = get_stage_next_free_path(self._current_stage, stage_prefix, False)
        self._usd_file = usd_path

        # Spawn the vehicle primitive in the world's stage
        self._prim = define_prim(self._stage_prefix, "Xform")
        self._prim = get_prim_at_path(self._stage_prefix)
        self._prim.GetReferences().AddReference(self._usd_file)

        carb.log_info("=====================================================")
        carb.log_info(f"Vehicle stage_prefix: {self._stage_prefix}")
        carb.log_info(f"Vehicle prim: {self._prim}")

        # Initialize the "Robot" class
        # Note: we need to change the rotation to have qw first, because NVidia
        # does not keep a standard of quaternions inside its own libraries (not good, but okay)
        super().__init__(
            prim_path=self._stage_prefix,
            name=self._stage_prefix,
            position=init_pos,
            orientation=[init_orientation[3], init_orientation[0], init_orientation[1], init_orientation[2]],
            articulation_controller=None,
        )

        # Add this object for the world to track, so that if we clear the world, this object is deleted from memory and
        # as a consequence, from the VehicleManager as well
        self._world.scene.add(self)

        # Add the current vehicle to the vehicle manager, so that it knows
        # that a vehicle was instantiated
        VehicleManager.get_vehicle_manager().add_vehicle(self._stage_prefix, self)

        # Variable that will hold the current state of the vehicle
        self._state = State()

        # Add a callback to the physics engine to update the current state of the system
        # self._world.add_physics_callback(self._stage_prefix + "/state", self.update_state)

        # Add the update method to the physics callback if the world was received
        # so that we can apply forces and torques to the vehicle. Note, this method should
        # be implemented in classes that inherit the vehicle object
        # self._world.add_physics_callback(self._stage_prefix + "/update", self.update)

        # Set the flag that signals if the simulation is running or not
        self._sim_running = False

        # Add a callback to start/stop of the simulation once the play/stop button is hit
        # self._world.add_timeline_callback(self._stage_prefix + "/start_stop_sim", self.sim_start_stop)

    def __del__(self):
        """
        Method that is invoked when a vehicle object gets destroyed. When this happens, we also invoke the
        'remove_vehicle' from the VehicleManager in order to remove the vehicle from the list of active vehicles.
        """

        # Remove this object from the vehicleHandler
        VehicleManager.get_vehicle_manager().remove_vehicle(self._stage_prefix)

    @property
    def state(self):
        """The state of the vehicle.

        Returns:
            State: The current state of the vehicle, i.e., position, orientation, linear and angular velocities...
        """
        return self._state

    def sim_start(self, event):
        """
        Callback that is called every time there is a timeline event such as starting the simulation.

        Args:
            event: A timeline event generated from Isaac Sim, such as starting the simulation.
        """

        # If the start/stop button was pressed, then call the start method accordingly
        if self._world.is_playing() and self._sim_running is False:
            self._sim_running = True
            self.start()

    def sim_stop(self, event):
        """
        Callback that is called every time there is a timeline event such as stopping the simulation.

        Args:
            event: A timeline event generated from Isaac Sim, such as stopping the simulation.
        """

        # If the start/stop button was pressed, then call the stop method accordingly
        if self._world.is_stopped() and self._sim_running is True:
            self._sim_running = False
            self.stop()

    def apply_torque(self, torque):
        pass

    def update_state(self, dt: float):
        """
        Method that is called at every physics step to retrieve and update the current state of the vehicle, i.e., get
        the current position, orientation, linear and angular velocities and acceleration of the vehicle.

        Args:
            dt (float): The time elapsed between the previous and current function calls (s).
        """

        # Get the body frame interface of the vehicle
        # (this will be the frame used to get the position, orientation, etc.)
        body = self._world.dc_interface.get_rigid_body(self._stage_prefix + "/body")

        # Get the current position and orientation in the inertial frame
        pose = self._world.dc_interface.get_rigid_body_pose(body)

        # Get the attitude according to the convention [w, x, y, z]
        prim = self._world.stage.GetPrimAtPath(self._stage_prefix + "/body")
        rotation_quat = get_world_transform_xform(prim).GetQuaternion()
        rotation_quat_real = rotation_quat.GetReal()
        rotation_quat_img = rotation_quat.GetImaginary()

        # Get the angular velocity of the vehicle expressed in the body frame of reference
        ang_vel = self._world.dc_interface.get_rigid_body_angular_velocity(body)

        # The linear velocity [x_dot, y_dot, z_dot] of the vehicle's body frame expressed
        # in the inertial frame of reference
        linear_vel = self._world.dc_interface.get_rigid_body_linear_velocity(body)

        # Get the linear acceleration of the body relative to the inertial frame, expressed in the inertial frame
        # Note: we must do this approximation, since the Isaac sim does not output
        # the acceleration of the rigid body directly
        linear_acceleration = (np.array(linear_vel) - self._state.linear_velocity) / dt

        # Update the state variable X = [x,y,z]
        self._state.position = np.array(pose.p)

        # Get the quaternion according in the [qx,qy,qz,qw] standard
        self._state.attitude = np.array(
            [rotation_quat_img[0], rotation_quat_img[1], rotation_quat_img[2], rotation_quat_real])

        # Express the velocity of the vehicle in the inertial frame X_dot = [x_dot, y_dot, z_dot]
        self._state.linear_velocity = np.array(linear_vel)

        # The linear velocity V =[u,v,w] of the vehicle's body frame expressed in the body frame of reference
        # Note that: x_dot = Rot * V
        self._state.linear_body_velocity = (Rotation.from_quat(self._state.attitude).inv().apply(
            self._state.linear_velocity))

        # omega = [p,q,r]
        self._state.angular_velocity = Rotation.from_quat(self._state.attitude).inv().apply(np.array(ang_vel))

        # The acceleration of the vehicle expressed in the inertial frame X_ddot = [x_ddot, y_ddot, z_ddot]
        self._state.linear_acceleration = linear_acceleration

    def start(self):
        """
        Method that should be implemented by the class that inherits the vehicle object.
        """
        pass

    def stop(self):
        """
        Method that should be implemented by the class that inherits the vehicle object.
        """
        pass

    def update(self, dt: float):
        """
        Method that computes and applies the forces to the vehicle in
        simulation based on the motor speed. This method must be implemented
        by a class that inherits this type and it's called periodically by the physics engine.

        Args:
            dt (float): The time elapsed between the previous and current function calls (s).
        """
        pass
