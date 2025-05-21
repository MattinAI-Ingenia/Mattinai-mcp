from typing import List, Tuple
from presidio_analyzer import AnalyzerEngine, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio.nlp_engine_factory import PresidioNlpEngineFactory

class PresidioProcessor:
    """A wrapper to use Presidio for PII detection and anonymization."""
    
    def __init__(
        self,
        model_family: str = "spaCy",
        model_name: str = "en_core_web_lg",
        threshold: float = 0.4,
        allow_list: List[str] = None,
        deny_list: List[str] = None,
    ):        
        # Get NLP engine and registry
        print('Getting nlp engine')
        nlp_engine, registry = PresidioNlpEngineFactory.get_nlp_engine(model_family, model_name)
        
        # Create analyzer
        self.analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry
        )
        
        # Create anonymizer
        self.anonymizer = AnonymizerEngine()
        
        # Store configuration 
        self.threshold = threshold
        self.allow_list = allow_list or []
        self.deny_list = deny_list or []

        # Add deny list recognizer if provided
        if self.deny_list:
            deny_list_recognizer = PatternRecognizer(
                supported_entity="GENERIC_PII", 
                deny_list=self.deny_list
            )
            self.analyzer.registry.add_recognizer(deny_list_recognizer)
        
        # Get supported entities
        self.supported_entities = self.analyzer.get_supported_entities()
    
    def analyze(self, text: str):
        """Analyze text for PII entities."""
        # Prepare analyze parameters
        analyze_params = {
            "text": text,
            "entities": "", # Detect all supported entities
            "language": "en",
            "score_threshold": self.threshold,
            "allow_list": self.allow_list
        }
        
        # Analyze the text
        results = self.analyzer.analyze(**analyze_params)
        return results
    
    def anonymize(
        self, 
        text: str, 
        analyze_results, 
        operator: str = "replace",
        mask_char: str = "*",
        number_of_chars: int = None,
        encrypt_key: str = "WmZq4t7w!z%C&F)J"
    ):
        """Anonymize PII entities in text."""
        from presidio_anonymizer.entities import OperatorConfig
        
        # Configure operator
        if operator == "mask":
            operator_config = {
                "type": "mask",
                "masking_char": mask_char,
                "chars_to_mask": number_of_chars,
                "from_end": False,
            }
        elif operator == "encrypt":
            operator_config = {"key": encrypt_key}
        elif operator == "highlight":
            operator_config = {"lambda": lambda x: x}
            operator = "custom"  # highlight is implemented as custom
        else:
            operator_config = None
        
        # Anonymize the text
        result = self.anonymizer.anonymize(
            text,
            analyze_results,
            operators={"DEFAULT": OperatorConfig(operator, operator_config)},
        )
        
        return result
    
    def process_text(
        self, 
        text: str, 
        operator: str = "replace",
        mask_char: str = "*",
        number_of_chars: int = None,
        encrypt_key: str = "WmZq4t7w!z%C&F)J",
    ):
        """Process text for PII detection and anonymization."""
        # Analyze the text
        analyze_results = self.analyze(text)
        
        # If no entities found, return original text
        if not analyze_results:
            return text, []
        
        # Prepare result entities for return
        entities = []
        for result in analyze_results:
            entity = result.to_dict()
            entity["text"] = text[result.start:result.end]
            entities.append(entity)
        
        # If we just want to highlight, return the original text and entities
        if operator == "highlight":
            return text, entities
        
        # For all other operators, anonymize the text
        anonymized_result = self.anonymize(
            text=text,
            analyze_results=analyze_results,
            operator=operator,
            mask_char=mask_char,
            number_of_chars=number_of_chars,
            encrypt_key=encrypt_key
        )
        
        return anonymized_result.text, entities