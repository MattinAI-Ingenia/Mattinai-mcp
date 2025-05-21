import logging
from typing import Tuple

import spacy
from presidio_analyzer import RecognizerRegistry
from presidio_analyzer.nlp_engine import (
    NlpEngine,
    NlpEngineProvider,
)

class PresidioNlpEngineFactory:
    @staticmethod
    def create_nlp_engine_with_spacy(
        model_path: str,
    ) -> Tuple[NlpEngine, RecognizerRegistry]:
        """
        Instantiate an NlpEngine with a spaCy model
        :param model_path: path to model / model name.
        """
        nlp_configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_path}],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON",
                    "PERSON": "PERSON",
                    "NORP": "NRP",
                    "FAC": "FACILITY",
                    "LOC": "LOCATION",
                    "GPE": "LOCATION",
                    "LOCATION": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "ORGANIZATION": "ORGANIZATION",
                    "DATE": "DATE_TIME",
                    "TIME": "DATE_TIME",
                },
                "low_confidence_score_multiplier": 0.4,
                "low_score_entity_names": ["ORG", "ORGANIZATION"],
            },
        }

        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

        registry = RecognizerRegistry()
        registry.load_predefined_recognizers(nlp_engine=nlp_engine)

        return nlp_engine, registry

    @staticmethod
    def create_nlp_engine_with_flair(
        model_path: str,
    ) -> Tuple[NlpEngine, RecognizerRegistry]:
        """
        Instantiate an NlpEngine with a FlairRecognizer and a small spaCy model.
        The FlairRecognizer would return results from Flair models, the spaCy model
        would return NlpArtifacts such as POS and lemmas.
        :param model_path: Flair model path.
        """
        from presidio.flair_recognizer import FlairRecognizer

        registry = RecognizerRegistry()
        registry.load_predefined_recognizers()

        # there is no official Flair NlpEngine, hence we load it as an additional recognizer

        if not spacy.util.is_package("en_core_web_sm"):
            spacy.cli.download("en_core_web_sm")
        # Using a small spaCy model + a Flair NER model
        flair_recognizer = FlairRecognizer(model_path=model_path)
        nlp_configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        registry.add_recognizer(flair_recognizer)
        registry.remove_recognizer("SpacyRecognizer")

        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()

        return nlp_engine, registry

    @staticmethod
    def get_nlp_engine(model_family: str, model_path: str):
        """Get the appropriate NLP engine based on the model family."""
        if "spacy" in model_family.lower():
            logging.info(f"Loading spaCy model: {model_path}")
            return PresidioNlpEngineFactory.create_nlp_engine_with_spacy(model_path)
        if "flair" in model_family.lower():
            logging.info(f"Loading spaCy model: {model_path}")
            return PresidioNlpEngineFactory.create_nlp_engine_with_flair(model_path)
        else:
            raise ValueError(f"Model family {model_family} not supported. Use 'spaCy'")
