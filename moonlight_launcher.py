#!/usr/bin/env python3
import os
import time
import yaml
import pyudev
import subprocess
import cec
import signal
from wakeonlan import send_magic_packet
from loguru import logger


def get_current_datetime():
    # e.g "15/06/2023 01:02:25 PM INFO: Example log entry"
    now = time.strftime("%d/%m/%Y %r", time.localtime())
    return now 


def read_log_path_from_config(config_file:str='config.yml'):

    """
    Get the log path from the config file - i.e where do we want to save the logs?

    :param config_file: name of the config file
    :return ret_path: path to log file 
    """

    ret_path = ''
    
    # get path to config file, assuming it's in the same folder
    script_path = os.path.realpath(os.path.dirname(__file__))
    config_path = os.path.join(script_path, config_file)

    # read in log path
    with open(config_path, 'r') as file:
        config = yaml.safe_load(file)
    
    ret_path = config.get('paths').get('log')
    
    return ret_path


def read_mac_address_from_config(config_file:str='config.yml', device_name:str='pc'):
 
    """
    Get the mac address from the config file (see readme).
    A bit overkill if you ask me.

    :param config_file: name of the config file
    :param device_name: what the device's 'name' attribute is called in the config file
    :return ret_mac: the mac address string 
    """
    
    ret_mac = ''
    
    # get path to config file, assuming it's in the same folder
    script_path = os.path.realpath(os.path.dirname(__file__))
    config_path = os.path.join(script_path, config_file)

    # read in mac address
    with open(config_path, 'r') as file:
        config = yaml.safe_load(file)
    
    for device in config.get('devices'):
        if device.get('name') == device_name:
            ret_mac = device.get('mac_address')
    
    return ret_mac


def turn_on_tv_and_switch_source():

    """Turn on the TV and switch source to this device.
    Note: this is by far the most brittle part of this whole script, thanks to CEC.
    """

    tv = cec.Device(cec.CECDEVICE_TV)

    # send 'on' message every second until TV is on, timeout after 10 seconds
    start_time = time.time()
    timeout = 10
    on = False
    while not on and time.time() < start_time + timeout:
        tv.power_on()
        time.sleep(1)
        on = tv.is_on()
        logger.debug(f"Sent 'on' message to TV after {int(time.time() - start_time)} seconds, state is currently 'on': {on}.")

    # switch source
    if on:
        cec.set_active_source()
        logger.debug("Sent 'set active source' to TV.")
    elif not on:
        logger.error("TV is still not on after 10s, what's going on?")
 
    # add a delay in case switching sources takes some time
    time.sleep(5)


def launch_moonlight():

    logger.info("Launching Moonlight.")

    try:
        bash = "moonlight"
        subprocess.run(bash)
        logger.success(f"Successfully ran the bash command '{bash}' to try to launch Moonlight.")
    except Exception as e:
        logger.error(f"Exception: {e}")


def turn_off_tv():

    """Turn off the TV (i.e put it on 'standby')
    """

    tv = cec.Device(cec.CECDEVICE_TV)
    tv.standby()
    logger.success("Successfully sent 'standby' signal to TV.")


def handle_event(action, device):

    """
    Asynchronously invoked whenever the underlying monitor receives an event.
    Logic to handle event:
    - Log that the event has been received by the handler
    - IF 'add' action is detected AND it's the first 'controller' device connected:
        - Send WOL
        - Turn on TV and switch channels
        - (Wait for 20s?)
        - Launch Moonlight
    - IF 'remove' action is detected AND it was the last 'controller' device:
        - Close Moonlight 
        - Turn off TV

    """

    try:

        logger.info("Observer detected a udev event, starting handling function.")
        logger.debug(f"Detected udev event '{action}' for device '{device.device_node}'.")

        # count how many controllers are connected
        count_controllers = 0
        for device in context.list_devices().match_property('SUBSYSTEM', 'hidraw').match_tag('controller'):
            count_controllers += 1
        logger.debug(f"Number of controllers detected: {count_controllers}.")

        if action == "add" and count_controllers == 1:
            # first controller added
            logger.debug("Handling logic: first controller added.")
            pc_mac = read_mac_address_from_config()
            send_magic_packet(pc_mac)
            logger.info(f"Sent wake-on-lan (WOL) packet to {pc_mac}.")
            turn_on_tv_and_switch_source()
            launch_moonlight()
            logger.success("Successfully handled logic: first controller added.")
        elif action == "remove" and count_controllers == 0:
            # last controller removed
            logger.debug("Handling logic: last controller removed.")
            turn_off_tv()
            logger.success("Successfully handled logic: last controller removed.")

    except Exception as e:
        logger.error(f"Error handling event: {e}")


def handle_stop_signals(signum, frame):

    signal_dict = {
        2: "SIGINT",  # Ctrl + C
        15: "SIGTERM"  # Signal to terminate the process (e.g from htop)
    }

    logger.debug(f"Detected stop code: {signum} ({signal_dict[int(signum)]})")
    logger.success("Successfully detected a stop signal, exiting the script...")
    exit()


if __name__ == "__main__":

    # set up logging
    log_path = read_log_path_from_config()
    logger.add(sink=log_path)
    logger.success("Script successfully loaded. Beginning main module.")

    # handle stop signals
    signal.signal(signal.SIGINT, handle_stop_signals)
    signal.signal(signal.SIGTERM, handle_stop_signals)  # TODO: how does this work with the new subprocess?
    
    try:

        cec.init()
        logger.info("Initialised cec session.")
        
        # Create udev events monitor
        logger.info("Creating udev events monitor.")
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        filter = 'controller'  # controller bluetooth devices are assigned this tag, as defined in udev rules
        monitor.filter_by_tag(filter)
        logger.success(f"Successfully created udev events monitor, filtering by {filter} tag.")
        logger.info(f"Note: the {filter} tag is assigned to controller bluetooth devices, as defined in udev rules in /etc/.")

        # Create observer and start asynchronous monitoring
        logger.info("Creating observer to watch the events monitor.")
        observer = pyudev.MonitorObserver(monitor, handle_event)
        observer.start()
        logger.success("Successfully created and started pyudev observer.")       
        
        # Keep the script running and log it...
        log_interval = 30  # minutes
        logger.info(f"Beginning standby phase, with {log_interval} minutes between logs entries.")
        while True:
            logger.debug("Script still running...")
            time.sleep(log_interval*60)  # (arg is in seconds)

    except Exception as e:
        logger.error(f"Quit unexpectedly! Exception: {e}")

