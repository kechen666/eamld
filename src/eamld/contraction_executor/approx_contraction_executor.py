from typing import List, Dict, Tuple, Set, Optional
import numpy as np
import stim
import logging
import time
from decimal import Decimal
from eamld.logging_config import setup_logger
from eamld.contraction_strategy.contraction_strategy import ContractionStrategy
from eamld.contraction_strategy.dem_to_hypergraph import DetectorErrorModelHypergraph
from collections import defaultdict
# from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import ProcessPoolExecutor  # 使用多进程
from enum import Enum

# 定义四种策略的枚举类型
class ApproximateStrategy(Enum):
    NODE_TOPK = 'node_topk'
    HYPEREDGE_TOPK = 'hyperedge_topk'
    NODE_THRESHOLD = 'node_threshold'
    HYPEREDGE_THRESHOLD = 'hyperedge_threshold'

# 存储每种策略对应的参数
strategy_params = {
    ApproximateStrategy.NODE_TOPK: 10**3,
    ApproximateStrategy.HYPEREDGE_TOPK: 10**4,
    ApproximateStrategy.NODE_THRESHOLD: 10**-8,
    ApproximateStrategy.HYPEREDGE_THRESHOLD: 10**-9
}

# 设置 logging 配置，放在模块级别
logger = setup_logger("contraction_executor/approx_contraction_executor", log_level=logging.WARNING)

def flip_bits(binary_tuple: Tuple[bool], hyperedge: Tuple[str], detector_number: int) -> Tuple[bool]:
    """
    Flip the specified bits in a binary tuple based on the hyperedge.

    Args:
        binary_tuple (Tuple[bool]): The binary sequence represented as a tuple of bits (e.g., 9 bits).
        hyperedge (Tuple[str]): The hyperedge specifying which bits to flip (e.g., ('D0', 'L1')).
        detector_number (int): The number of detector bits (used to calculate indices for 'L' labels).

    Returns:
        Tuple[bool]: The binary sequence after flipping the specified bits.

    Raises:
        ValueError: If an invalid bit label format is encountered in the hyperedge.
    """
    binary_list = list(binary_tuple)
    index_map = {}
    for bit_label in hyperedge:
        if bit_label[0] == 'D':  # 减少 startswith 调用
            index = int(bit_label[1:])
        elif bit_label[0] == 'L':
            index = detector_number + int(bit_label[1:])
        else:
            raise ValueError(f"Invalid bit label '{bit_label}': must start with 'D' or 'L'.")
        index_map[bit_label] = index

    for index in index_map.values():
        binary_list[index] = not binary_list[index]
    return tuple(binary_list)


class ApproximateContractionExecutor:
    def __init__(self, detector_error_model: stim.DetectorErrorModel, contraction_strategy: ContractionStrategy,
                 use_decimal: bool = False, approximatestrategy: str = "node_topk",
                 approximate_param: Optional[int | float] = None):
        """
        Initialize the ContractionExecutor with the given detector error model and contraction strategy.

        Args:
            detector_error_model (stim.DetectorErrorModel): The detector error model used for contraction.
            contraction_strategy (ContractionStrategy): The contraction strategy used for decoding.
            :param use_decimal: 布尔值，指示是否使用 Decimal 类型进行数值计算，默认为 False。
            :param approximatestrategy: 近似策略的字符串表示，默认为 "node_topk"。
            :param approximate_param: 近似策略的参数，可以是整数（如 top-k 中的 k）或浮点数（如阈值），默认为 None。
        """
        self.detector_error_model = detector_error_model
        self.detector_number = detector_error_model.num_detectors
        self.logical_number = detector_error_model.num_observables
        self.contraction_strategy = contraction_strategy
        self.order = contraction_strategy.order
        self.sliced_hyperedges = contraction_strategy.sliced_hyperedges
        self.use_decimal = use_decimal
        
        self.max_before_approx_prob_dist_size: int = 0
        
        self._execution_contraction_time: float = 0
        self._execution_max_distribution_size: int = 0
        
        # 近似策略以及近似参数
        valid_strategies = ["node_topk", "hyperedge_topk", "node_threshold", "hyperedge_threshold"]
        if approximatestrategy not in valid_strategies:
            raise ValueError(f"approximatestrategy 必须是以下之一: {valid_strategies}")
        self.approximatestrategy: str = approximatestrategy
        self.approximatestrategy_method: str = approximatestrategy.split("_")[1]
        self.approximate_position: str = approximatestrategy.split("_")[0]
        
        # 验证 approximate_param 的有效性
        if approximate_param is not None:
            if approximatestrategy.endswith("_topk") and not isinstance(approximate_param, int):
                raise ValueError(f"当 approximatestrategy 为 {approximatestrategy} 时，approximate_param 必须是整数。")
            if approximatestrategy.endswith("_threshold") and not isinstance(approximate_param, (int, float)):
                raise ValueError(f"当 approximatestrategy 为 {approximatestrategy} 时，approximate_param 必须是数字。")
            self.approximate_param = approximate_param
        else:
            self.approximate_param = strategy_params[ApproximateStrategy(approximatestrategy)]

        
    def _to_numeric_type(self, value):
        if self.use_decimal:
            return Decimal(str(value))
        return float(value)

    def approximate_distribution(self, updated_prob_dist: Dict[Tuple[bool], float | Decimal],) -> Dict[Tuple[bool], float | Decimal]:
        """
        Approximate the probability distribution based on the specified strategy and parameter.
        """
        approximatestrategy_splited = self.approximatestrategy.split("_")
        approximatestrategy_method = approximatestrategy_splited[1]
        if approximatestrategy_method == "topk":
            if len(updated_prob_dist) <= self.approximate_param:
                logger.info(f"没有执行更新操作，概率分布为{len(updated_prob_dist)} 小于 {self.approximate_param}")
                return updated_prob_dist
            # print("updated_prob_dist's shape", len(updated_prob_dist))
            logger.info(f"执行了更新操作，更新前的概率分布大小为{len(updated_prob_dist)}")
            updated_prob_dist = sorted(updated_prob_dist.items(), key=lambda item: item[1], reverse=True)[:self.approximate_param]
            updated_prob_dist = dict(updated_prob_dist)
            # print("after updated_prob_dist's shape", len(updated_prob_dist))
            logger.info(f"topk更新后的概率分布大小为{len(updated_prob_dist)}")
        elif approximatestrategy_method == "threshold":
            # print("approximatestrategy_method", approximatestrategy_method)
            threshold = self._to_numeric_type(self.approximate_param)
            updated_prob_dist = dict((key, value) for key, value in updated_prob_dist.items() if value > threshold)
            logger.info(f"threshold更新后的概率分布大小为{len(updated_prob_dist)}")
        else:
            raise ValueError(f"Invalid approximate strategy: {self.approximatestrategy}")
        
        return updated_prob_dist

    def get_task_initial_input(self) -> Tuple[Dict[Tuple[bool], float | Decimal], Dict[Tuple[str], float | Decimal]]:
        """
        Get the initial input for the task, including the probability distribution
        and the hyperedge weights dictionary.

        This method initializes the probability distribution with a single deterministic entry
        (all False for both detectors and logical qubits). It also prepares the hyperedge weights dictionary.

        Returns:
            Tuple[Dict[Tuple[bool], float | Decimal], Dict[Tuple[str], float | Decimal]]:
                - Initial probability distribution.
                - Dictionary of contractable hyperedge weights.
        """
        init_prob_dist: Dict[Tuple[bool], float | Decimal] = {}
        contractable_hyperedges_weights_dict: Dict[Tuple[str], float | Decimal] = {}
        
        # Initialize the key with all False values
        init_key = tuple([False] * self.detector_number + [False] * self.logical_number)
        init_prob_dist[init_key] = self._to_numeric_type(1)
        
        # Generate hyperedge weights dictionary
        contractable_hyperedges_weights_dict = self.get_hyperedges_weights_dict()
        
        return init_prob_dist, contractable_hyperedges_weights_dict

    def get_parallel_task_initial_input(self) -> Tuple[Dict[Tuple[bool], float | Decimal],
                                                       Dict[Tuple[str], float | Decimal]]:
        """
        Generate the initial input for parallel task execution.

        This method computes the initial probability distribution and the hyperedge weights dictionary
        after slicing, preparing them for parallel task execution.

        Returns:
            Tuple[Dict[Tuple[bool], float | Decimal], Dict[Tuple[str], float | Decimal]]:
                - The parallelizable initial probability distribution.
                - The dictionary of contractable hyperedge weights.
        """
        sliced_hyperedges: List[Tuple[str]] = self.sliced_hyperedges
        parallelizable_init_prob_dist, contractable_hyperedges_weights_dict = self.get_task_initial_input()

        # Process each sliced hyperedge to update the probability distribution and hyperedge weights
        for hyperedge in sliced_hyperedges:
            parallelizable_init_prob_dist, contractable_hyperedges_weights_dict = self.contract_hyperedge(
                parallelizable_init_prob_dist, contractable_hyperedges_weights_dict, hyperedge
            )

        return parallelizable_init_prob_dist, contractable_hyperedges_weights_dict

    def contract_hyperedge(
        self,
        prob_dist: Dict[Tuple[bool], float | Decimal],
        contractable_hyperedges_weights_dict: Dict[Tuple[str], float | Decimal],
        contracted_hyperedge: Tuple[str]
    ) -> Tuple[Dict[Tuple[bool], float | Decimal], Dict[Tuple[str], float | Decimal]]:
        """
        Contract a hyperedge and update the probability distribution and hyperedge weights dictionary.

        Args:
            prob_dist (Dict[Tuple[bool], float | Decimal]): Current probability distribution.
            contractable_hyperedges_weights_dict (Dict[Tuple[str], float | Decimal]): Hyperedge weights dictionary.
            contracted_hyperedge (Tuple[str]): The hyperedge to contract.

        Returns:
            Tuple[Dict[Tuple[bool], float | Decimal], Dict[Tuple[str], float | Decimal]]:
                - Updated probability distribution.
                - Updated hyperedge weights dictionary.
        """
        # Get the probability of the contracted hyperedge
        contracted_hyperedge_prob = contractable_hyperedges_weights_dict.pop(contracted_hyperedge)
        
        # Create a defaultdict to store the updated probability distribution (default to 0.0)
        if self.use_decimal:
            updated_prob_dist = defaultdict(lambda: Decimal('0'))
        else:
            updated_prob_dist = defaultdict(float)
        
        # Iterate over the current probability distribution
        for binary_tuple, prob in prob_dist.items():
            # Flip the bits for the current hyperedge
            flipped_tuple = flip_bits(binary_tuple, contracted_hyperedge, self.detector_number)
            
            # Calculate the updated probabilities based on the hyperedge contraction
            flipped_prob = prob * contracted_hyperedge_prob
            non_flipped_prob = prob * (self._to_numeric_type(1) - contracted_hyperedge_prob)

            # Add probabilities to the new distribution (defaultdict automatically handles the accumulation)
            updated_prob_dist[flipped_tuple] += flipped_prob
            updated_prob_dist[binary_tuple] += non_flipped_prob

        # Convert defaultdict back to a regular dict before returning and approximate the distribution
        if self.approximate_position == "hyperedge":
            logger.info(f"Before hyperedge approximate_distribution, prob_dist's shape: {len(prob_dist)}")
            updated_prob_dist = self.approximate_distribution(dict(updated_prob_dist))
        else:
            updated_prob_dist = dict(updated_prob_dist)
        
        return updated_prob_dist, contractable_hyperedges_weights_dict


    def mld_contraction_no_slicing(self, syndrome: np.ndarray[bool], order: Optional[List[str]] = None) -> Dict[
        Tuple[bool], float | Decimal]:
        """
        Perform MLD contraction without slicing.

        This method computes the MLD contraction based on the provided order, without applying slicing.
        The contraction is performed serially according to the specified order.

        Args:
            syndrome (np.ndarray[bool]): The syndrome to be processed.
            order (List[str], optional): The order in which the contraction should be performed.
                                        If not provided, the default order (self.order) will be used.

        Returns:
            Dict[Tuple[bool], float | Decimal]: The resulting probability distribution after the contraction.
        """
        # Validate input syndrome type
        if not isinstance(syndrome, np.ndarray) or syndrome.ndim != 1 or not np.issubdtype(syndrome.dtype, np.bool_):
            raise TypeError("syndrome must be a 1D np.ndarray of boolean values.")

        logger.debug(f"Calling {self.mld_contraction_no_slicing.__name__}")
        logger.debug(f"Contraction strategy: Using order: {self.order if order is None else order}")

        # Get the initial probability distribution and contractable hyperedges weights
        init_prob_dist, init_contractable_hyperedges_weights_dict = self.get_task_initial_input()
        
        start_time = time.time()
        # Perform the MLD contraction on the syndrome
        prob_dist, _ = self.single_node_online_mld_contraction(
            syndrome=syndrome,
            init_prob_dist=init_prob_dist,
            init_contractable_hyperedges_weights=init_contractable_hyperedges_weights_dict
        )
        self._execution_contraction_time = time.time() - start_time
        return prob_dist

    def mld_contraction_serial(self, syndrome: np.ndarray[bool]) -> Dict[Tuple[bool], float | Decimal]:
        """
        Perform serial MLD (Maximum Likelihood Decoding) contraction for a single syndrome.

        This method applies MLD contraction serially to a single syndrome, using the initial probability distribution
        obtained after slicing. No parallelization is involved in this process.

        Args:
            syndrome (np.ndarray[bool]): A 1D NumPy array representing the syndrome, where each element is a boolean value.

        Returns:
            Dict[Tuple[bool], float | Decimal]: A dictionary representing the probability distribution after contraction.
                                        Keys are tuples of boolean values, and values are the corresponding probabilities.

        Raises:
            TypeError: If the `syndrome` is not a 1D NumPy array of boolean values.
        """
        # Validate input syndrome type
        if not isinstance(syndrome, np.ndarray) or syndrome.ndim != 1 or not np.issubdtype(syndrome.dtype, np.bool_):
            raise TypeError("syndrome must be a 1D np.ndarray of boolean values.")

        logger.debug(f"Calling {self.mld_contraction_serial.__name__}")
        logger.debug(f"Contraction strategy: No parallelization, strategy: {self.contraction_strategy}")

        # Get the initial probability distribution and contractable hyperedges weights for serial computation
        parallelizable_init_prob_dist, init_contractable_hyperedges_weights_dict = self.get_parallel_task_initial_input()
        
        start_time = time.time()
        # Perform the MLD contraction on the syndrome
        prob_dist, _ = self.single_node_online_mld_contraction(
            syndrome=syndrome,
            init_prob_dist=parallelizable_init_prob_dist,
            init_contractable_hyperedges_weights=init_contractable_hyperedges_weights_dict
        )
        self._execution_contraction_time = time.time() - start_time
        return prob_dist

    # def mld_contraction_parallel_concurrent(
    #     self,
    #     syndrome: np.ndarray[bool],
    #     max_thread: int = 4
    # ) -> Dict[Tuple[bool], float | Decimal]:
    #     """
    #     Perform data-parallel MLD contraction.

    #     This method splits the parallelizable initial probability distribution into
    #     separate tasks and runs them in parallel using threading.

    #     Args:
    #         syndrome (np.ndarray[bool]): A 1D NumPy array representing the syndrome.
    #         max_thread (int): The maximum number of threads to use for parallelization. Default is 4.

    #     Returns:
    #         Dict[Tuple[bool], float | Decimal]: The merged probability distribution after contraction.
    #     #TODO: 目前采用的可能是多线程的实现, 可能需要进行多进程的实现。同时似乎并行的优化效果不大。
    #     """
    #     # Validate input syndrome type
    #     if not isinstance(syndrome, np.ndarray) or syndrome.ndim != 1 or not np.issubdtype(syndrome.dtype, np.bool_):
    #         raise TypeError("syndrome must be a 1D np.ndarray of boolean values.")
        
    #     logger.debug(f"Calling {self.mld_contraction_parallel_concurrent.__name__} with max_thread={max_thread}")
        
    #     # Get the initial probability distribution and hyperedges weights
    #     parallelizable_init_prob_dist, init_contractable_hyperedges_weights_dict = self.get_parallel_task_initial_input()

    #     # Split the tasks based on the parallelizable probability distribution
    #     tasks = [
    #         (key, value) for key, value in parallelizable_init_prob_dist.items()
    #     ]
        
    #     start_time = time.time()
    #     # Parallel execution
    #     results = []
    #     with ThreadPoolExecutor(max_workers=max_thread) as executor:
    #         futures = [
    #             executor.submit(
    #                 self.single_node_online_mld_contraction,
    #                 syndrome=syndrome,
    #                 init_prob_dist={key: value},
    #                 init_contractable_hyperedges_weights=init_contractable_hyperedges_weights_dict
    #             )
    #             for key, value in tasks
    #         ]
    #         for future in futures:
    #             results.append(future.result())

    #     # Merge the results from all parallel tasks
    #     if self.use_decimal:
    #         merged_prob_dist = defaultdict(lambda: Decimal('0'))
    #     else:
    #         merged_prob_dist = defaultdict(float)
    #     for result in results:
    #         for key, value in result[0].items():
    #             merged_prob_dist[key] += value
    #     self._execution_contraction_time = time.time() - start_time
        
    #     return dict(merged_prob_dist)
    
    def mld_contraction_parallel_concurrent(
        self,
        syndrome: np.ndarray[bool],
        max_thread: int = 4
    ) -> Dict[Tuple[bool], float | Decimal]:
        """
        Perform data-parallel MLD contraction.

        This method splits the parallelizable initial probability distribution into
        separate tasks and runs them in parallel using threading.

        Args:
            syndrome (np.ndarray[bool]): A 1D NumPy array representing the syndrome.
            max_thread (int): The maximum number of threads to use for parallelization. Default is 4.

        Returns:
            Dict[Tuple[bool], float | Decimal]: The merged probability distribution after contraction.
        #TODO: 目前采用的可能是多线程的实现, 可能需要进行多进程的实现。同时似乎并行的优化效果不大。
        """
        # Validate input syndrome type
        if not isinstance(syndrome, np.ndarray) or syndrome.ndim != 1 or not np.issubdtype(syndrome.dtype, np.bool_):
            raise TypeError("syndrome must be a 1D np.ndarray of boolean values.")
        
        logger.debug(f"Calling {self.mld_contraction_parallel_concurrent.__name__} with max_thread={max_thread}")
        
        # Get the initial probability distribution and hyperedges weights
        parallelizable_init_prob_dist, init_contractable_hyperedges_weights_dict = self.get_parallel_task_initial_input()

        # Split the tasks based on the parallelizable probability distribution
        tasks = [
            (key, value) for key, value in parallelizable_init_prob_dist.items()
        ]
        
        start_time = time.time()
        # 多进程并行执行
        with ProcessPoolExecutor(max_workers=max_thread) as executor:
            futures = [
                executor.submit(
                    self.single_node_online_mld_contraction,
                    syndrome=syndrome,
                    init_prob_dist={key: value},
                    init_contractable_hyperedges_weights=init_contractable_hyperedges_weights_dict
                )
                for key, value in tasks
            ]
            results = [future.result() for future in futures]

        # Merge the results from all parallel tasks
        if self.use_decimal:
            merged_prob_dist = defaultdict(lambda: Decimal('0'))
        else:
            merged_prob_dist = defaultdict(float)
        for result in results:
            for key, value in result[0].items():
                merged_prob_dist[key] += value
        self._execution_contraction_time = time.time() - start_time
        
        return dict(merged_prob_dist)
    
    def single_node_online_mld_contraction(
            self,
            syndrome: np.ndarray[bool],
            init_prob_dist: Dict[Tuple[bool, ...], float | Decimal],
            init_contractable_hyperedges_weights: Dict[Tuple[str], float | Decimal]
    ) -> Dict[Tuple[bool], float | Decimal]:
        """
        Perform single-node online MLD contraction.

        This method computes the probability distribution for a given syndrome and updates it
        based on the initial probability distribution and hyperedge weights.

        Args:
            syndrome (np.ndarray[bool]): The measurement values of the syndrome bits.
                It should be a binary numpy array representing the syndrome.
            init_prob_dist (Dict[Tuple[bool, ...], float | Decimal]): Initial probability distribution.
                If there is no slicing, it defaults to {[0,...,0]: 1}. Otherwise, it may be transformed.
            init_contractable_hyperedges_weights (Dict[Tuple[str], float | Decimal]): The weights associated with each hyperedge to contract.

        Returns:
            Dict[Tuple[bool], float | Decimal]: The updated probability distribution for the given syndrome.
                The size of the distribution depends on the number of logical qubits.
        """
        logger.debug("Starting contraction for syndrome: %s", syndrome)
        
        # Initialize the probability distribution and hyperedge weights
        prob_dist = init_prob_dist.copy()  # Avoid modifying the input directly
        contractable_hyperedges_weights = init_contractable_hyperedges_weights.copy()
        
        # Iterate over each detector in the contraction order
        for contraction_step in range(self.detector_number):
            # Get the current contract detector and its index
            contract_detector = self.order[contraction_step]
            contract_detector_index = int(contract_detector[1:])  # Extract the index from the detector name
            observed_detector_syndrome = syndrome[contract_detector_index]

            logger.debug(f"Processing detector {contract_detector} (index {contract_detector_index}) with observed syndrome {observed_detector_syndrome}")
            
            # Contract all hyperedges connected to the current detector
            relevant_hyperedges = [
                hyperedge for hyperedge in contractable_hyperedges_weights.keys() 
                if contract_detector in hyperedge
            ]

            for hyperedge in relevant_hyperedges:
                # Perform contraction and update the probability distribution
                prob_dist, contractable_hyperedges_weights = self.contract_hyperedge(
                    prob_dist, contractable_hyperedges_weights, hyperedge
                )
            
            # Filter out candidates where the syndrome bit does not match the observed syndrome
            prob_dist = {
                candidate_syndrome: prob for candidate_syndrome, prob in prob_dist.items()
                if candidate_syndrome[contract_detector_index] == observed_detector_syndrome
            }
            if self.max_before_approx_prob_dist_size < len(prob_dist):
                self.max_before_approx_prob_dist_size = len(prob_dist)
                
            if self.approximate_position == "node":
                logger.info(f"Before node approximate_distribution, prob_dist's shape: {len(prob_dist)}")
                prob_dist = self.approximate_distribution(prob_dist)
                

            # Log current state after processing the detector
            logger.debug(f"Contraction step {contraction_step}, contract_detector: {contract_detector}")
            logger.debug(f"Updated prob_dist: {prob_dist}")
            logger.debug(f"Remaining hyperedges: {list(contractable_hyperedges_weights.keys())}")
            
        return prob_dist, contractable_hyperedges_weights
    
    @classmethod
    def validate_logical_operator(cls, prob_dist: Dict[Tuple[bool], float | Decimal], logical_operators: str = 'X') -> Tuple[np.ndarray[bool], float | Decimal]:
        """
        Validate if a logical error (logical operator) occurs by comparing the values of probability distributions.

        This method checks if a logical error has occurred by comparing the resulting probability distribution
        with the logical operators provided ('X' or 'Z'). It returns a logical error indicator and the probability
        of correct error correction.

        Args:
            prob_dist (Dict[Tuple[bool], float | Decimal]): The resulting probability distribution after contraction.
            logical_operators (str): A string indicating the logical operator to check against the distribution.
                Can be 'X' or 'Z' (default is 'X').

        Returns:
            Tuple[np.ndarray[bool], float | Decimal]:
                - A numpy array indicating whether a logical error is detected (True if logical error occurs).
                - The probability of correct error correction for the current syndrome.
        """
        keys = list(prob_dist.keys())
        
        # # Ensure the dictionary contains exactly two keys, with the last element being True and False
        # if len(keys) != 2:
        #     raise ValueError(f"prob_dist is {prob_dist}, must contain exactly two keys, with the last element being True and False.")
        
        # 近似可能会导致 prob_dist 的长度不为 2
        if len(keys) == 1:
            logical_error_detected = keys[0][-1]
            return np.array([logical_error_detected], dtype=bool), 1.0
        elif len(keys) == 0:
            # 如果没有keys，证明，经过近似之后，该syndrome发生的概率为0，因此默认返回不出现逻辑错误率，纠错正确概率为0.5
            return np.array([False], dtype=bool), 0.5
        
        # Extract p_1 (False) and p_2 (True)
        p_1 = None
        p_2 = None
        for key in keys:
            if key[-1] == False:
                p_1 = prob_dist[key]
            elif key[-1] == True:
                p_2 = prob_dist[key]
        
        # Ensure both p_1 and p_2 are successfully extracted
        if p_1 is None or p_2 is None:
            raise ValueError("prob_dist must contain keys with the last element being True and False.")
        
        # Compare p_1 and p_2 to determine if a logical error occurred
        logical_error_detected = p_2 > p_1
        
        # Calculate the probability of correct error correction
        # Here, we assume the probability of correct error correction is the higher of p_1 and p_2
        prob_correct_correction = max(p_1, p_2) / (p_1+p_2)
        
        # Return the logical error indicator and the probability of correct error correction
        return np.array([logical_error_detected], dtype=bool), prob_correct_correction

    def get_hyperedges_weights_dict(self) -> Dict[Tuple[str], float | Decimal]:
        """存储当前需要收缩的边以及边的权重的字典

        Returns:
            Dict[Tuple[str], float | Decimal]: {超边: 发生概率}
        """
        hypergraph = DetectorErrorModelHypergraph(self.detector_error_model, have_logical_observable=True)
        original_dict = hypergraph.get_hyperedges_weights_dict()
        return {key: self._to_numeric_type(value) for key, value in original_dict.items()}